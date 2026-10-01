from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, time

from .codmon.models import CodmonDailyContent, DailyReport

_LOGGER = logging.getLogger(__name__)

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_RANGE_RE = re.compile(r"(?P<start>\d{1,2}:\d{2})\s*(?:〜|~|-|－)\s*(?P<end>\d{1,2}:\d{2})")
_AMOUNT_RE = re.compile(r"ミルク\s*(\d+)\s*(?:ml|cc|ミリリットル)", re.IGNORECASE)

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

_MEAL_KEYWORDS = (
    "朝ごはん",
    "朝食",
    "午前おやつ",
    "昼食",
    "給食",
    "離乳食",
    "おやつ",
    "午後おやつ",
    "夕食",
    "夕ごはん",
)

_OUTDOOR_KEYWORDS = ("散歩", "公園", "園庭", "外遊び", "戸外")

_DEFAULT_MILK_TIME = time(10, 0)


@dataclass
class MilkEvent:
    time: time | None = None
    amount_ml: float | None = None
    note: str = ""


@dataclass
class SleepEvent:
    start: time | None = None
    end: time | None = None
    note: str = ""


@dataclass
class MealEvent:
    label: str
    foods: list[str] = field(default_factory=list)
    time: time | None = None
    note: str = ""


@dataclass
class ActivityEvent:
    description: str


@dataclass
class TemperatureEvent:
    time: time | None
    value: float
    source_note: str = ""


@dataclass
class PooEvent:
    time: time | None = None
    value: str = ""


@dataclass
class DailySummary:
    date: date
    milk_events: list[MilkEvent] = field(default_factory=list)
    sleep_events: list[SleepEvent] = field(default_factory=list)
    meal_events: list[MealEvent] = field(default_factory=list)
    activities: list[ActivityEvent] = field(default_factory=list)
    temperature_events: list[TemperatureEvent] = field(default_factory=list)
    poo_events: list[PooEvent] = field(default_factory=list)
    bathing: str = ""
    raw_text: str = ""


def _parse_hhmm(text: str) -> time | None:
    match = _TIME_RE.search(text)
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def _normalize_width(text: str) -> str:
    return text.translate(_FULLWIDTH_DIGITS)


def _meal_label(line: str) -> str | None:
    for keyword in sorted(_MEAL_KEYWORDS, key=len, reverse=True):
        if keyword in line:
            return keyword
    return None


def _meal_note_from_line(line: str, label: str) -> str:
    note = line.replace(label, "", 1)
    note = _AMOUNT_RE.sub("", note)
    note = re.sub(r"^[\s:：、.・]+", "", note)
    note = re.sub(r"\s+", " ", note).strip()
    return note


def extract_summary(report: DailyReport) -> DailySummary:
    summary = DailySummary(date=report.date)
    for post in report.posts:
        if post.body_text:
            summary.raw_text += post.body_text + "\n"
        if post.content is None:
            continue
        _parse_content(summary, post.content)
    return summary


def _parse_content(summary: DailySummary, content: CodmonDailyContent) -> None:
    numbers_normalized = _normalize_width(content.meal)
    for raw_line in numbers_normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        amounts = [float(amount) for amount in re.findall(_AMOUNT_RE, line)]
        label = _meal_label(line)
        line_time = _parse_hhmm(line)

        for amount in amounts:
            summary.milk_events.append(
                MilkEvent(
                    time=line_time or _milk_time(label) or _DEFAULT_MILK_TIME,
                    amount_ml=amount,
                    note=line,
                )
            )

        if label is not None:
            summary.meal_events.append(
                MealEvent(
                    label=label,
                    foods=[label],
                    time=line_time or _meal_time(label),
                    note=_meal_note_from_line(line, label),
                )
            )
        elif amounts and not _meal_time(label):
            _LOGGER.debug("Meal line without meal keyword: %r", line)

    if content.sleepings:
        normalized_sleepings = _normalize_width(content.sleepings)
        for start, end in re.findall(_RANGE_RE, normalized_sleepings):
            start_time = _parse_hhmm(start)
            end_time = _parse_hhmm(end)
            if start_time and end_time:
                summary.sleep_events.append(SleepEvent(start=start_time, end=end_time, note=content.sleepings))

    if content.memo_text:
        summary.activities.append(ActivityEvent(description=content.memo_text))
    summary.bathing = content.bathing

    for measurement in content.tempratures:
        value = measurement.get("temprature")
        if value is None:
            continue
        try:
            parsed = float(value)
        except TypeError, ValueError:
            continue
        summary.temperature_events.append(
            TemperatureEvent(
                time=_parse_hhmm(str(measurement.get("temprature_time") or "")),
                value=parsed,
                source_note=str(value),
            )
        )

    for evacuation in content.evacuations:
        value = str(evacuation.get("evacuation") or "").strip()
        if not value or value == "不明":
            continue
        summary.poo_events.append(
            PooEvent(
                time=_parse_hhmm(str(evacuation.get("evacuation_time") or "")),
                value=value,
            )
        )


def _meal_time(label: str | None) -> time | None:
    return _MEAL_TIMES.get(label) if label else None


def _milk_time(label: str | None) -> time | None:
    if not label:
        return None
    return _MILK_TIMES.get(label, _MEAL_TIMES.get(label))


_MEAL_TIMES = {
    "朝ごはん": time(9, 0),
    "朝食": time(9, 0),
    "午前おやつ": time(10, 0),
    "昼食": time(11, 15),
    "給食": time(11, 15),
    "離乳食": time(11, 15),
    "午後おやつ": time(15, 15),
    "おやつ": time(15, 15),
    "夕食": time(18, 0),
    "夕ごはん": time(18, 0),
}

_MILK_TIMES = {
    "給食": time(11, 45),
    "午後おやつ": time(15, 30),
}
