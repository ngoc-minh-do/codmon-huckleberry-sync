from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .codmon.api import CodmonApiClient
from .codmon.client import CodmonClient
from .codmon.models import DailyReport
from .config import Config
from .hb.writer import HuckleberryWriter
from .mapping import plan_events
from .parser import extract_summary

_LOGGER = logging.getLogger(__name__)

WINDOW_DAYS_BEFORE = 7


@dataclass
class SyncResult:
    day: date
    dry_run: bool
    posts: int = 0
    milk: int = 0
    sleep: int = 0
    meal: int = 0
    activity: int = 0
    temperature: int = 0
    poo: int = 0
    planned: int = 0
    translated: int = 0
    written: int = 0
    written_by_kind: dict[str, int] = field(default_factory=dict)
    skipped_by_kind: dict[str, int] = field(default_factory=dict)
    events: list = field(default_factory=list)


@dataclass
class BackfillResult:
    start: date
    end: date
    dry_run: bool
    days: int = 0
    days_failed: int = 0
    written: int = 0
    written_by_kind: dict[str, int] = field(default_factory=dict)


async def introspect(cfg: Config, target: date | None, output: str | None) -> DailyReport:
    if cfg.codmon_transport == "api":
        return await _introspect_api(cfg, target, output)
    return await _introspect_browser(cfg, target, output)


async def _introspect_api(cfg: Config, target: date | None, output: str | None) -> DailyReport:
    output_dir = cfg.data_dir / (output or "introspect")
    output_dir.mkdir(parents=True, exist_ok=True)
    day = target or date.today()
    client = CodmonApiClient(cfg.codmon_email, cfg.codmon_password)
    await client.start()
    try:
        report = await client.fetch_daily_report(day)
        await _write_introspect_api(output_dir, report)
    finally:
        await client.stop()
    _LOGGER.info("Introspection complete: %s posts written to %s", len(report.posts), output_dir)
    return report


async def _write_introspect_api(output_dir, report: DailyReport) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, post in enumerate(report.posts):
        content = post.content
        block = f"=== post {index} (id={post.post_id}) ===\n"
        if content:
            block += f"memo:\n{content.memo_text}\nmeal:\n{content.meal}\nsleepings:\n{content.sleepings}\n"
            block += f"tempratures: {content.tempratures}\n"
        lines.append(block)
    import json

    output_dir.joinpath("api_report.txt").write_text("\n\n".join(lines), encoding="utf-8")
    output_dir.joinpath("network.json").write_text(
        json.dumps(report.network, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _introspect_browser(cfg: Config, target: date | None, output: str | None) -> DailyReport:
    output_dir = cfg.data_dir / (output or "introspect")
    output_dir.mkdir(parents=True, exist_ok=True)
    client = CodmonClient(
        cfg.codmon_email,
        cfg.codmon_password,
        headless=cfg.headless,
        record_har_path=str(output_dir / "network.har"),
    )
    await client.start()
    try:
        report = await client.introspect(output_dir, target=target)
    finally:
        await client.stop()
    _LOGGER.info("Introspection complete: %s artifacts written to %s", len(report.posts), output_dir)
    return report


async def sync_day(
    cfg: Config,
    target: date | None,
    *,
    dry_run: bool | None = None,
    child: str | None = None,
) -> SyncResult:
    effective_dry_run = cfg.dry_run if dry_run is None else dry_run
    child = child or cfg.child
    day = target or date.today()

    report = await _fetch_report(cfg, day, child)
    if not report.posts:
        _LOGGER.warning("No Codmon daily report posts found for %s", day)
        return SyncResult(day=day, dry_run=effective_dry_run)

    summary = extract_summary(report)
    events = plan_events(
        summary,
        cfg.timezone,
        sync_temperature=cfg.sync_temperature,
        sync_diaper=cfg.sync_diaper,
        sync_bath=cfg.sync_bath,
        bath_time=cfg.bath_time,
    )
    translated = await _maybe_translate(cfg, events)
    result = SyncResult(
        day=day,
        dry_run=effective_dry_run,
        posts=len(report.posts),
        milk=len(summary.milk_events),
        sleep=len(summary.sleep_events),
        meal=len(summary.meal_events),
        activity=len(summary.activities),
        temperature=len(summary.temperature_events),
        poo=len(summary.poo_events),
        planned=len(events),
        translated=translated,
        events=events,
    )
    _LOGGER.info(
        "Parsed %s: %d milk, %d sleep, %d meal, %d activity, %d temperature, %d poo -> %d Huckleberry events (dry_run=%s)",
        day,
        result.milk,
        result.sleep,
        result.meal,
        result.activity,
        result.temperature,
        result.poo,
        result.planned,
        effective_dry_run,
    )
    if translated:
        _LOGGER.info(
            "Translated %d of %d activity description(s) via %s", translated, len(summary.activities), cfg.llm_model
        )
    if not events:
        _LOGGER.warning("No parseable events for %s", day)
        return result

    writer = HuckleberryWriter(cfg, dry_run=effective_dry_run)
    try:
        await writer.connect(child=child)
        applied = await writer.apply(events, day)
    finally:
        await writer.close()

    result.written = applied.written
    result.written_by_kind = applied.written_by_kind
    result.skipped_by_kind = applied.skipped_by_kind
    return result


async def backfill(
    cfg: Config,
    start: date,
    end: date | None,
    *,
    dry_run: bool | None = None,
    child: str | None = None,
) -> BackfillResult:
    effective_dry_run = cfg.dry_run if dry_run is None else dry_run
    child = child or cfg.child
    day_end = end or date.today()

    client = CodmonApiClient(cfg.codmon_email, cfg.codmon_password)
    await client.start()
    try:
        reports = await client.fetch_reports_range(start, day_end, child=child)
    finally:
        await client.stop()

    days = sorted(reports)
    _LOGGER.info("Backfill found %d report days in %s..%s (dry_run=%s)", len(days), start, day_end, effective_dry_run)

    result = BackfillResult(start=start, end=day_end, dry_run=effective_dry_run, days=len(days))

    translator = _new_translator(cfg)
    try:
        writer = HuckleberryWriter(cfg, dry_run=effective_dry_run)
        try:
            await writer.connect(child=child)
            total_written = 0
            days_failed = 0
            written_by_kind: dict[str, int] = defaultdict(int)
            for day in days:
                try:
                    summary = extract_summary(reports[day])
                    events = plan_events(
                        summary,
                        cfg.timezone,
                        sync_temperature=cfg.sync_temperature,
                        sync_diaper=cfg.sync_diaper,
                        sync_bath=cfg.sync_bath,
                        bath_time=cfg.bath_time,
                    )
                    if translator is not None:
                        await _translate_events(translator, events)
                    _LOGGER.info(
                        "--- %s: %d milk, %d sleep, %d meal, %d activity, %d temperature, %d poo -> %d events",
                        day,
                        len(summary.milk_events),
                        len(summary.sleep_events),
                        len(summary.meal_events),
                        len(summary.activities),
                        len(summary.temperature_events),
                        len(summary.poo_events),
                        len(events),
                    )
                    if events:
                        applied = await writer.apply(events, day)
                        total_written += applied.written
                        for kind, count in applied.written_by_kind.items():
                            written_by_kind[kind] += count
                except Exception:
                    days_failed += 1
                    _LOGGER.exception("Backfill failed for %s", day)
            result.days_failed = days_failed
            result.written = total_written
            result.written_by_kind = dict(written_by_kind)
            _LOGGER.info(
                "Backfill complete: wrote %d events across %d days (%d failed)",
                total_written,
                len(days) - days_failed,
                days_failed,
            )
            return result
        finally:
            await writer.close()
    finally:
        if translator is not None:
            await translator.close()


async def _fetch_report(cfg: Config, day: date, child: str | None) -> DailyReport:
    if cfg.codmon_transport == "browser":
        client = CodmonClient(cfg.codmon_email, cfg.codmon_password, headless=cfg.headless)
        await client.start()
        try:
            return await client.fetch_daily_report(day)
        finally:
            await client.stop()

    client = CodmonApiClient(cfg.codmon_email, cfg.codmon_password)
    await client.start()
    try:
        return await client.fetch_daily_report(day, child=child)
    finally:
        await client.stop()


async def _maybe_translate(cfg: Config, events: list) -> int:
    translator = _new_translator(cfg)
    if translator is None:
        return 0
    before = sum(1 for event in events if event.kind == "activity")
    try:
        await _translate_events(translator, events)
    finally:
        await translator.close()
    return before


def _new_translator(cfg: Config):
    if not cfg.translate_activity:
        return None
    from .translate import ActivityTranslator

    return ActivityTranslator.from_config(cfg)


async def _translate_events(translator, events: list) -> None:
    from .translate import translate_activity_descriptions

    await translate_activity_descriptions(translator, events)
