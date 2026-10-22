from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from codmon_sync import sync as sync_module
from codmon_sync.codmon.api import CodmonApiClient
from codmon_sync.config import Config
from codmon_sync.hb.writer import ApplyResult
from codmon_sync.mapping import plan_events
from codmon_sync.parser import (
    ActivityEvent,
    DailySummary,
    MealEvent,
    MilkEvent,
    PooEvent,
    parse_growth_records,
)
from codmon_sync.translate import _looks_truncated, _split_paragraphs

TZ = ZoneInfo("Asia/Tokyo")


def test_split_paragraphs_ignores_blank_lines():
    assert _split_paragraphs("one\n\ntwo\n\n\nthree") == ["one", "two", "three"]
    assert _split_paragraphs("  solo  ") == ["solo"]
    assert _split_paragraphs("") == []


def test_looks_truncated_only_for_long_sources():
    source = "あ" * 200
    assert _looks_truncated(source, "short")
    assert not _looks_truncated(source, source)
    assert not _looks_truncated("small", "x")
    assert not _looks_truncated(source, source + " extra" * 40)


def test_parse_growth_records_merges_same_day():
    raw = [
        {"insert_datetime": "2026-04-01 09:30:00", "height": "70.5", "weight": "8.2"},
        {"insert_datetime": "2026-04-01 09:30:00", "head": "45.0", "chest": "48.0"},
    ]
    records = parse_growth_records(raw)
    assert len(records) == 1
    record = records[0]
    assert (record.height, record.weight, record.head, record.chest) == (70.5, 8.2, 45.0, 48.0)


def _summary() -> DailySummary:
    summary = DailySummary(date=date(2026, 10, 7))
    summary.milk_events.append(MilkEvent(time=time(15, 30), amount_ml=120, note="ミルク120cc"))
    summary.meal_events.append(MealEvent(label="給食", foods=["給食"], time=time(11, 15), note="完食"))
    summary.poo_events.append(PooEvent(time=time(12, 20), value="普通"))
    return summary


def test_plan_events_maps_core_kinds():
    events = plan_events(_summary(), TZ)
    kinds = {event.kind for event in events}
    assert {"bottle", "solids", "diaper"} <= kinds
    bottle = next(event for event in events if event.kind == "bottle")
    assert bottle.payload["amount_ml"] == 120
    diaper = next(event for event in events if event.kind == "diaper")
    assert diaper.payload["consistency"] == "solid"


def test_plan_events_activity_mode_from_keywords():
    summary = DailySummary(date=date(2026, 10, 7))
    summary.activities.append(ActivityEvent(description="今日は公園まで散歩しました"))
    events = plan_events(summary, TZ)
    activity = next(event for event in events if event.kind == "activity")
    assert activity.payload["mode"] == "outdoorPlay"


def test_plan_events_skips_unknown_evacuation():
    summary = DailySummary(date=date(2026, 10, 7))
    summary.poo_events.append(PooEvent(time=time(9, 0), value="なんか"))
    events = plan_events(summary, TZ)
    assert all(event.kind != "diaper" for event in events)


def _api_client_with_timeline(monkeypatch, items, recorded):
    """CodmonApiClient stub whose timeline returns fixed items and records requests."""
    client = CodmonApiClient("a@b.c", "pw")

    async def fake_ready(child=None):
        client._service_ids = ["s1"]

    async def fake_page(start, end, service_id, page=1):
        recorded.append((start.isoformat(), end.isoformat()))
        return {"data": items, "next_page": None}

    monkeypatch.setattr(client, "_ensure_ready", fake_ready)
    monkeypatch.setattr(client, "_timeline_page", fake_page)
    return client


@pytest.mark.asyncio
async def test_fetch_daily_report_widens_window_to_catch_late_opened_posts(monkeypatch):
    # A report whose display_date is Oct 21 but which was opened/published on
    # Oct 22 must still be found: the timeline range request has to reach today.
    items = [
        {"id": "111", "kind": "4", "display_date": "2026-10-21", "content": ""},
        {"id": "222", "kind": "4", "display_date": "2026-10-22", "content": ""},
    ]
    recorded = []
    client = _api_client_with_timeline(monkeypatch, items, recorded)

    report = await client.fetch_daily_report(date(2026, 10, 21), today=date(2026, 10, 22))

    assert recorded == [("2026-10-21", "2026-10-22")]
    assert [post.post_id for post in report.posts] == ["111"]  # display_date must match target
    assert [item["id"] for item in report.raw_items] == ["111", "222"]


@pytest.mark.asyncio
async def test_fetch_daily_report_same_day_window_unchanged(monkeypatch):
    recorded = []
    client = _api_client_with_timeline(monkeypatch, [], recorded)

    await client.fetch_daily_report(date(2026, 10, 22), today=date(2026, 10, 22))

    assert recorded == [("2026-10-22", "2026-10-22")]


@pytest.mark.asyncio
async def test_fetch_daily_report_window_is_capped_for_old_targets(monkeypatch):
    # An old target must not paginate the whole timeline up to today: the
    # widened window is bounded to target + 7 days.
    recorded = []
    client = _api_client_with_timeline(monkeypatch, [], recorded)

    await client.fetch_daily_report(date(2020, 1, 1), today=date(2026, 10, 22))

    assert recorded == [("2020-01-01", "2020-01-08")]


def _config(tmp_path) -> Config:
    return Config(
        codmon_email="a@b.c",
        codmon_password="pw",
        huckleberry_email="h@b.c",
        huckleberry_password="pw",
        timezone_name="Asia/Tokyo",
        dry_run=False,
        data_dir=tmp_path,
        headless=True,
        sync_temperature=True,
        sync_diaper=True,
        sync_bath=True,
        bath_time=time(9, 30),
        codmon_transport="api",
        child=None,
        dedup_window_minutes=15,
        translate_activity=False,
        llm_base_url="",
        llm_model="chat-default",
        llm_api_key=None,
        llm_timeout=30,
        apprise_url=None,
    )


@pytest.mark.asyncio
async def test_backfill_writes_growth_records(monkeypatch, tmp_path):
    class FakeCodmon:
        async def start(self):
            return None

        async def stop(self):
            return None

        async def fetch_reports_range(self, start, end, child=None):
            return {}

        async def fetch_growth_records(self, start, end, child=None):
            return [{"insert_datetime": "2026-10-21 09:30:00", "height": "70.5", "weight": "8.2"}]

    applied_ranges = []

    class FakeWriter:
        def __init__(self, config, *, dry_run):
            pass

        async def connect(self, child=None):
            return None

        async def apply(self, events, day):
            return ApplyResult(written=0, written_by_kind={}, skipped_by_kind={})

        async def apply_range(self, events, start, end):
            applied_ranges.append((start, end, events))
            return ApplyResult(
                written=len(events),
                written_by_kind={
                    kind: sum(1 for event in events if event.kind == kind) for kind in {e.kind for e in events}
                },
                skipped_by_kind={},
            )

        async def close(self):
            return None

    monkeypatch.setattr(sync_module, "CodmonApiClient", lambda email, password: FakeCodmon())
    monkeypatch.setattr(sync_module, "HuckleberryWriter", FakeWriter)

    result = await sync_module.backfill(_config(tmp_path), date(2026, 10, 21), date(2026, 10, 22))

    assert result.growth == 1
    assert result.written == 1
    assert result.written_by_kind == {"growth": 1}
    assert len(applied_ranges) == 1
    start, end, events = applied_ranges[0]
    assert (start, end) == (date(2026, 10, 21), date(2026, 10, 22))
    assert [event.kind for event in events] == ["growth"]
    assert events[0].start.date() == date(2026, 10, 21)
