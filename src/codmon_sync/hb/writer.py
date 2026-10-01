from __future__ import annotations

import logging
import time as _time
import uuid
from datetime import date, datetime, time, timedelta

import aiohttp

from ..config import Config, ConfigError
from ..mapping import PlannedEvent, solids_reference

_LOGGER = logging.getLogger(__name__)


def _without_none(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if value is not None}


class HuckleberryWriter:
    def __init__(self, config: Config, *, dry_run: bool) -> None:
        self.config = config
        self.dry_run = dry_run
        self._session: aiohttp.ClientSession | None = None
        self._api = None
        self._child_uid: str | None = None

    async def connect(self, child: str | None = None) -> None:
        from huckleberry_api import HuckleberryAPI

        self._session = aiohttp.ClientSession()
        self._api = HuckleberryAPI(
            email=self.config.huckleberry_email,
            password=self.config.huckleberry_password,
            timezone=self.config.timezone_name,
            websession=self._session,
        )
        user = await self._api.get_user()
        if not user.childList:
            raise ConfigError("Huckleberry account has no children registered")
        self._child_uid = self._resolve_child_uid(user.childList, child)
        _LOGGER.info("Connected to Huckleberry as %s (child=%s)", user.email, self._child_uid)

    @staticmethod
    def _resolve_child_uid(child_list: list, child: str | None) -> str:
        if not child:
            return child_list[0].cid
        needle = child.strip().lower()
        for ref in child_list:
            if ref.cid == needle:
                return ref.cid
            nickname = (ref.nickname or "").strip().lower()
            if nickname and needle in nickname:
                return ref.cid
        raise ConfigError(f"No Huckleberry child matches {child!r}")

    async def apply(self, events: list[PlannedEvent], day: date) -> int:
        if not self._api or not self._child_uid:
            raise RuntimeError("HuckleberryWriter.connect() must be called before apply()")
        existing = await self._collect_existing(day)
        window_seconds = self.config.dedup_window_minutes * 60
        written = 0
        for event in events:
            kind_key = self._kind_key(event)
            start_label = event.start.astimezone(self.config.timezone).strftime("%Y-%m-%d %H:%M")
            duplicates = [
                start_sec
                for existing_kind, start_sec in existing
                if existing_kind == kind_key and abs(start_sec - event.start.timestamp()) <= window_seconds
            ]
            if duplicates:
                dup_time = datetime.fromtimestamp(min(duplicates), tz=self.config.timezone).strftime("%Y-%m-%d %H:%M")
                _LOGGER.info("SKIP %s at %s (already in Huckleberry at %s)", event.kind, start_label, dup_time)
                continue
            _LOGGER.info("PLAN %s at %s %s", event.kind, start_label, event.payload)
            if not self.dry_run:
                await self._dispatch(event)
                written += 1
        return written

    async def _collect_existing(self, day: date) -> list[tuple[str, float]]:
        if not self._api or not self._child_uid:
            return []
        client = await self._api._get_firestore_client()
        child = self._child_uid

        day_start = datetime.combine(day, time(0, 0), tzinfo=self.config.timezone)
        earliest = day_start.timestamp() - 24 * 3600
        latest = (day_start + timedelta(days=1)).timestamp() + 24 * 3600

        collections = [
            ("feed", "intervals"),
            ("sleep", "intervals"),
            ("activities", "intervals"),
            ("health", "data"),
            ("diaper", "intervals"),
        ]

        existing: list[tuple[str, float]] = []
        for top, sub in collections:
            try:
                subref = client.collection(top).document(child).collection(sub)
                async for doc in subref.stream():
                    for entry in self._entries(doc.to_dict() or {}):
                        start = entry.get("start")
                        try:
                            start_sec = float(start)
                        except TypeError, ValueError:
                            continue
                        if start_sec < earliest or start_sec > latest:
                            continue
                        kind = self._entry_kind(top, entry)
                        if kind:
                            existing.append((kind, start_sec))
            except Exception as exc:
                _LOGGER.warning("Could not read %s/%s history: %s", top, sub, exc)
        return existing

    @staticmethod
    def _entries(raw: dict) -> list[dict]:
        if raw.get("multi") is True:
            data = raw.get("data")
            if isinstance(data, dict):
                return [value for value in data.values() if isinstance(value, dict)]
            return []
        return [raw]

    @staticmethod
    def _entry_kind(top: str, entry: dict) -> str | None:
        mode = entry.get("mode")
        if top == "feed":
            if mode == "bottle":
                return "bottle"
            if mode == "solids":
                return "solids"
            return None
        if top == "sleep":
            return "sleep"
        if top == "activities":
            return f"activity:{mode}" if mode else None
        if top == "health":
            return "temperature" if mode == "temperature" else None
        if top == "diaper":
            return "diaper"
        return None

    @staticmethod
    def _kind_key(event: PlannedEvent) -> str:
        if event.kind == "activity":
            return f"activity:{event.payload.get('mode', 'indoorPlay')}"
        return event.kind

    def _tz_offset_minutes(self) -> float:
        now = datetime.now(self.config.timezone)
        offset = now.utcoffset()
        if offset is None:
            return 0.0
        return -offset.total_seconds() / 60

    async def _write_temperature(self, event: PlannedEvent) -> None:
        client = await self._api._get_firestore_client()
        child = self._child_uid
        amount = float(event.payload["amount"])
        units = event.payload.get("units", "C")
        start_timestamp = event.start.timestamp()
        current_time = _time.time()
        offset = self._tz_offset_minutes()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"

        entry = {
            "mode": "temperature",
            "start": start_timestamp,
            "lastUpdated": current_time,
            "offset": offset,
            "amount": amount,
            "units": units,
        }
        health_ref = client.collection("health").document(child)
        await health_ref.collection("data").document(interval_id).set(_without_none(entry))

        health_doc = await health_ref.get()
        raw = health_doc.to_dict() or {}
        existing = (raw.get("prefs") or {}).get("lastTemperature") or {}
        existing_start = existing.get("start")
        should_update = existing_start is None or start_timestamp >= float(existing_start)
        if should_update:
            last = {
                "_id": interval_id,
                "type": "health",
                "mode": "temperature",
                "start": start_timestamp,
                "lastUpdated": current_time,
                "offset": offset,
                "amount": amount,
                "units": units,
                "multientry_key": None,
            }
            await health_ref.set(
                {
                    "prefs": {
                        "lastTemperature": last,
                        "timestamp": {"seconds": current_time},
                        "local_timestamp": current_time,
                    }
                },
                merge=True,
            )

    async def _dispatch(self, event: PlannedEvent) -> None:
        assert self._api is not None and self._child_uid is not None
        api = self._api
        child = self._child_uid
        try:
            if event.kind == "bottle":
                await api.log_bottle(
                    child,
                    start_time=event.start,
                    amount=event.payload["amount_ml"],
                    bottle_type=event.payload.get("bottle_type", "Formula"),
                    units="ml",
                )
            elif event.kind == "sleep":
                await api.log_sleep(
                    child,
                    start_time=event.start,
                    end_time=event.end,
                )
            elif event.kind == "solids":
                await api.log_solids(
                    child,
                    start_time=event.start,
                    foods=[solids_reference(name) for name in event.payload["foods"]],
                    notes=event.payload.get("note", ""),
                )
            elif event.kind == "activity":
                await api.log_activity(
                    child,
                    mode=event.payload.get("mode", "indoorPlay"),
                    start_time=event.start,
                    duration=event.payload.get("duration"),
                    notes=event.payload.get("description", ""),
                )
            elif event.kind == "temperature":
                await self._write_temperature(event)
            elif event.kind == "diaper":
                await api.log_diaper(
                    child,
                    start_time=event.start,
                    mode=event.payload.get("mode", "both"),
                    pee_amount=event.payload.get("pee_amount"),
                    poo_amount=event.payload.get("poo_amount"),
                    consistency=event.payload.get("consistency"),
                )
            else:
                raise ValueError(f"Unknown event kind {event.kind}")
            _LOGGER.info("WROTE %s at %s", event.kind, event.start.isoformat())
        except Exception:
            _LOGGER.exception("Failed to write Huckleberry event %s", event)
            raise

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
