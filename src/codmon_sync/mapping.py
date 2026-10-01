from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from huckleberry_api.models import SolidsFoodReference

from .parser import DailySummary

_LOGGER = logging.getLogger(__name__)

EventKind = Literal["bottle", "sleep", "solids", "activity", "temperature", "diaper"]

_OUTDOOR_KEYWORDS = ("散歩", "公園", "園庭", "外遊び", "戸外")
_ACTIVITY_START = time(9, 30)

_POO_CONSISTENCY = {
    "普通": "solid",
    "軟便": "loose",
    "下痢便": "diarrhea",
    "硬便": "hard",
    "少量便": "solid",
}


@dataclass
class PlannedEvent:
    kind: EventKind
    start: datetime
    end: datetime | None
    payload: dict


def plan_events(
    summary: DailySummary,
    tz: ZoneInfo,
    *,
    sync_temperature: bool = True,
    sync_diaper: bool = True,
    sync_bath: bool = True,
    bath_time: time = time(9, 30),
) -> list[PlannedEvent]:
    events: list[PlannedEvent] = []
    day = summary.date

    for milk in summary.milk_events:
        if milk.amount_ml is None or milk.time is None:
            _LOGGER.info("Skipping milk event without amount/time: %r", milk.note)
            continue
        bottle_type = "Breast Milk" if "母乳" in milk.note else "Formula"
        events.append(
            PlannedEvent(
                kind="bottle",
                start=_combine(day, milk.time, tz),
                end=None,
                payload={"amount_ml": milk.amount_ml, "bottle_type": bottle_type},
            )
        )

    for sleep in summary.sleep_events:
        if sleep.start is None or sleep.end is None:
            _LOGGER.info("Skipping sleep event without full range: %r", sleep.note)
            continue
        start = _combine(day, sleep.start, tz)
        end = _combine(day, sleep.end, tz)
        if start is None or end is None or end <= start:
            continue
        events.append(PlannedEvent(kind="sleep", start=start, end=end, payload={}))

    for meal in summary.meal_events:
        if meal.time is None:
            _LOGGER.info("Skipping meal event without time: %r", meal.note)
            continue
        foods = meal.foods or [meal.label]
        events.append(
            PlannedEvent(
                kind="solids",
                start=_combine(day, meal.time, tz),
                end=None,
                payload={"foods": foods, "note": meal.note},
            )
        )

    for activity in summary.activities:
        if not activity.description.strip():
            continue
        mode = "indoorPlay"
        for outdoor_word in _OUTDOOR_KEYWORDS:
            if outdoor_word in activity.description:
                mode = "outdoorPlay"
                break
        events.append(
            PlannedEvent(
                kind="activity",
                start=_combine(day, _ACTIVITY_START, tz),
                end=None,
                payload={"mode": mode, "description": activity.description},
            )
        )

    if sync_temperature:
        for measurement in summary.temperature_events:
            if measurement.time is None:
                _LOGGER.info("Skipping temperature without time: %r", measurement.source_note)
                continue
            events.append(
                PlannedEvent(
                    kind="temperature",
                    start=_combine(day, measurement.time, tz),
                    end=None,
                    payload={"amount": measurement.value, "units": "C", "note": measurement.source_note},
                )
            )

    if sync_diaper:
        for poo in summary.poo_events:
            if poo.time is None:
                _LOGGER.info("Skipping evacuation without time: %r", poo.value)
                continue
            consistency = _POO_CONSISTENCY.get(poo.value)
            if consistency is None:
                _LOGGER.info("Skipping evacuation with unknown wording: %r", poo.value)
                continue
            events.append(
                PlannedEvent(
                    kind="diaper",
                    start=_combine(day, poo.time, tz),
                    end=None,
                    payload={
                        "mode": "both",
                        "pee_amount": "medium",
                        "poo_amount": "little" if poo.value == "少量便" else "medium",
                        "consistency": consistency,
                        "note": poo.value,
                    },
                )
            )

    if sync_bath and summary.bathing == "有":
        events.append(
            PlannedEvent(
                kind="activity",
                start=_combine(day, bath_time, tz),
                end=None,
                payload={"mode": "bath", "description": "水遊び"},
            )
        )

    return events


def _combine(day, value: time | None, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(day, value, tzinfo=tz)


def solids_reference(food_name: str) -> SolidsFoodReference:
    return SolidsFoodReference(id=str(uuid.uuid4()), source="custom", name=food_name, amount=0)
