from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

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
