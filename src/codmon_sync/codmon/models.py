from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*/?\s*(?:br|p|div|li|h[1-6])\s*/?\s*>", re.IGNORECASE)


def strip_html(value: str) -> str:
    value = _BR_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    return html.unescape(value).strip()


@dataclass
class CodmonDailyContent:
    memo_text: str = ""
    meal: str = ""
    sleepings: str = ""
    tempratures: list[dict] = field(default_factory=list)
    evacuations: list[dict] = field(default_factory=list)
    bathing: str = ""
    mood_morning: str = ""
    mood_afternoon: str = ""

    @classmethod
    def from_raw(cls, raw: str | None) -> CodmonDailyContent:
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError, TypeError:
            return cls(memo_text=strip_html(raw))
        if not isinstance(data, dict):
            return cls()
        memo_text = strip_html(str(data.get("memo") or ""))
        return cls(
            memo_text=memo_text,
            meal=str(data.get("meal") or ""),
            sleepings=str(data.get("sleepings") or ""),
            tempratures=list(data.get("tempratures") or []),
            evacuations=list(data.get("evacuations") or []),
            bathing=str(data.get("bathing") or ""),
            mood_morning=str(data.get("mood_morning") or ""),
            mood_afternoon=str(data.get("mood_afternoon") or ""),
        )


@dataclass
class CodmonPost:
    post_id: str
    body_text: str
    raw_html: str = ""
    detail_url: str = ""
    content: CodmonDailyContent | None = None


@dataclass
class DailyReport:
    date: date
    posts: list[CodmonPost] = field(default_factory=list)
    network: list[dict] = field(default_factory=list)
