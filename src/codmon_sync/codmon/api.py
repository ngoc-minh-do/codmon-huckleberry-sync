from __future__ import annotations

import logging
from datetime import date
from typing import Any

import aiohttp

from .models import CodmonDailyContent, CodmonPost, DailyReport

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://ps-api.codmon.com"
ENV_PARAM = "__env__"
ENV_VALUE = "myapp"

TIMEOUT = aiohttp.ClientTimeout(total=60)


class CodmonError(RuntimeError):
    pass


class CodmonApiClient:
    """Read-only client for Codmon's internal parent API (parents.codmon.com web app).

    Uses the same JSON endpoints the web app calls, authenticated with the
    session cookies the login endpoint issues. No browser required.
    """

    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self._session: aiohttp.ClientSession | None = None
        self._ready = False
        self._service_ids: list[str] = []
        self._children: list[dict] = []

    async def start(self) -> CodmonApiClient:
        jar = aiohttp.CookieJar()
        self._session = aiohttp.ClientSession(cookie_jar=jar, timeout=TIMEOUT)
        return self

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict:
        if self._session is None:
            raise RuntimeError("CodmonApiClient.start() must be called first")
        url = f"{API_BASE}{path}"
        async with self._session.request(method, url, params=params, json=json_body) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
        if not isinstance(body, dict):
            raise CodmonError(f"Unexpected response shape from {path}")
        if body.get("success") is False:
            raise CodmonError(f"Codmon API error from {path}: {body.get('error')}")
        return body

    async def login(self) -> None:
        body = await self._request(
            "POST",
            "/api/v2/parent/login",
            params={ENV_PARAM: ENV_VALUE},
            json_body={
                "login_id": self.email,
                "login_password": self.password,
                "use_db_replica": 1,
            },
        )
        if not body.get("success"):
            raise CodmonError(f"Codmon login failed: {body.get('error')}")
        _LOGGER.info("Codmon API login succeeded")

    async def children(self) -> list[dict]:
        body = await self._request(
            "GET",
            "/api/v2/parent/children/",
            params={ENV_PARAM: ENV_VALUE, "use_db_replica": 1, "use_image_edge": "true"},
        )
        return body.get("data", [])

    @staticmethod
    def _children_for(children: list[dict], child: str | None) -> list[dict]:
        """Return the child documents to sync, honoring the optional CHILD match."""
        if child:
            needle = child.strip().lower()
            for child_doc in children:
                name = str(child_doc.get("name") or "").lower()
                kana = str(child_doc.get("kana") or "").lower()
                child_id = str(child_doc.get("id") or "")
                for candidate in (child_id, name, kana):
                    if needle == candidate or (candidate and needle in candidate):
                        return [child_doc]
            raise CodmonError(f"No Codmon child matches {child!r}")
        if children:
            return [children[0]]
        return []

    @staticmethod
    def _relation_values(children: list[dict], child: str | None, key: str) -> list[str]:
        values: list[str] = []
        for child_doc in CodmonApiClient._children_for(children, child):
            for relation in child_doc.get("child_member_relations", []):
                value = relation.get(key)
                if value and value not in values:
                    values.append(value)
        return values

    @classmethod
    def _service_ids_for(cls, children: list[dict], child: str | None) -> list[str]:
        return cls._relation_values(children, child, "service_id")

    @classmethod
    def _member_ids_for(cls, children: list[dict], child: str | None) -> list[str]:
        return cls._relation_values(children, child, "member_id")

    async def _timeline_page(self, start: date, end: date, service_id: str, page: int) -> dict:
        params = [
            (ENV_PARAM, ENV_VALUE),
            ("listpage", str(page)),
            ("search_type[]", "new_all"),
            ("start_date", start.isoformat()),
            ("end_date", end.isoformat()),
            ("service_id", service_id),
            ("current_flag", "0"),
            ("use_image_edge", "true"),
            ("bookmark_only", "false"),
        ]
        return await self._request("GET", "/api/v2/parent/timeline/", params=params)

    async def fetch_daily_report(self, target: date, child: str | None = None) -> DailyReport:
        await self._ensure_ready(child)

        posts: list[CodmonPost] = []
        network: list[dict] = []
        seen: set[str] = set()

        for service_id in self._service_ids:
            page = 1
            while True:
                body = await self._timeline_page(target, target, service_id, page=page)
                network.append({"service_id": service_id, "page": page})
                for item in body.get("data", []):
                    parsed = self._report_post(item)
                    if parsed is None:
                        continue
                    day, post = parsed
                    if day != target or post.post_id in seen:
                        continue
                    seen.add(post.post_id)
                    posts.append(post)
                next_page = body.get("next_page")
                if not isinstance(next_page, int) or next_page <= page:
                    break
                page = next_page
            _LOGGER.info("Timeline for %s: %d daily-report posts", target, len(posts))

        return DailyReport(date=target, posts=posts, network=network)

    async def fetch_reports_range(
        self,
        start: date,
        end: date,
        child: str | None = None,
    ) -> dict[date, DailyReport]:
        """Fetch every daily report in [start, end] as a {date: DailyReport} map.

        Single paginated scan per nursery; the timeline items carry the full
        report content, so no per-day requests are needed.
        """
        await self._ensure_ready(child)

        grouped: dict[date, list[CodmonPost]] = {}
        seen: set[tuple[str, str]] = set()

        for service_id in self._service_ids:
            page = 1
            while True:
                body = await self._timeline_page(start, end, service_id, page=page)
                for item in body.get("data", []):
                    parsed = self._report_post(item)
                    if parsed is None:
                        continue
                    day, post = parsed
                    if day < start or day > end:
                        continue
                    key = (service_id, post.post_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    grouped.setdefault(day, []).append(post)
                next_page = body.get("next_page")
                if not isinstance(next_page, int) or next_page <= page:
                    break
                page = next_page

        reports = {day: DailyReport(date=day, posts=posts) for day, posts in grouped.items()}
        _LOGGER.info("Scanned %s..%s: %d days with reports", start, end, len(reports))
        return reports

    async def _ensure_ready(self, child: str | None) -> None:
        if self._ready:
            return
        await self.login()
        children = await self.children()
        self._children = children
        self._service_ids = self._service_ids_for(children, child)
        if not self._service_ids:
            raise CodmonError("No nursery service found for this Codmon account")
        self._ready = True

    async def fetch_growth_records(
        self,
        start: date,
        end: date,
        child: str | None = None,
    ) -> list[dict]:
        """Fetch the child's growth-measurement records (成長記録) in [start, end].

        Returns raw records: each has ``insert_datetime`` (actual measurement
        day; ``record_date`` is only a month label), and any of ``height``
        (cm), ``weight`` (kg), ``chest`` (cm), ``head`` (cm).
        """
        await self._ensure_ready(child)
        member_ids = self._member_ids_for(self._children, child)
        if not member_ids:
            return []

        records: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for member_id in member_ids:
            params = [
                (ENV_PARAM, ENV_VALUE),
                ("search_start_record_date", start.isoformat()),
                ("search_end_record_date", end.isoformat()),
                ("relation_id[]", member_id),
            ]
            body = await self._request("GET", "/api/v2/parent/member_growths/", params=params)
            for item in body.get("data", []):
                key = (member_id, str(item.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                records.append(item)

        records.sort(key=lambda record: str(record.get("record_date") or ""))
        _LOGGER.info("Growth records: %d measurements in %s..%s", len(records), start, end)
        return records

    def _report_post(self, item: dict) -> tuple[date, CodmonPost] | None:
        if str(item.get("kind")) != "4":
            return None
        try:
            day = date.fromisoformat(str(item.get("display_date", ""))[:10])
        except ValueError:
            return None
        content = CodmonDailyContent.from_raw(item.get("content"))
        texts = [content.memo_text, content.meal, content.sleepings]
        post = CodmonPost(
            post_id=str(item.get("id", "")),
            body_text="\n".join(t for t in texts if t),
            raw_html=str(item.get("content") or ""),
            content=content,
        )
        return day, post
