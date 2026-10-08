from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass

import aiohttp

from .config import Config

_LOGGER = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You translate the daily nursery report memo from Japanese into natural, warm, "
    "concise English for a baby-tracking app. Preserve proper nouns. Never add "
    "information that is not in the source. If the text contains no Japanese, "
    "return it unchanged."
)

_BATCH_SIZE = 15

_TRUNCATION_RATIO = 0.5
_TRUNCATION_MIN_SOURCE = 150


def _split_paragraphs(text: str) -> list[str]:
    parts = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text)]
    return [paragraph for paragraph in parts if paragraph]


def _looks_truncated(source: str, translation: str) -> bool:
    if not source or not translation:
        return False
    if translation == source:
        return False
    if len(source) < _TRUNCATION_MIN_SOURCE:
        return False
    return len(translation) < len(source) * _TRUNCATION_RATIO


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout: int = 180


class ActivityTranslator:
    def __init__(self, config: LlmConfig | None) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, str] = {}

    @classmethod
    def from_config(cls, cfg: Config) -> ActivityTranslator:
        if not cfg.translate_activity:
            return cls(config=None)
        return cls(
            config=LlmConfig(
                base_url=cfg.llm_base_url,
                model=cfg.llm_model,
                api_key=cfg.llm_api_key,
                timeout=cfg.llm_timeout,
            )
        )

    async def translate(self, texts: list[str]) -> dict[str, str]:
        if not self.config:
            return {text: text for text in texts}
        missing = [text for text in texts if text and text not in self._cache]
        if missing:
            for chunk_start in range(0, len(missing), _BATCH_SIZE):
                chunk = missing[chunk_start : chunk_start + _BATCH_SIZE]
                translated = await self._translate_memos(chunk)
                for text in chunk:
                    candidate = translated.get(text, text)
                    if _looks_truncated(text, candidate):
                        _LOGGER.warning(
                            "Truncated translation for memo (%d chars) -> %d chars; falling back to original",
                            len(text),
                            len(candidate),
                        )
                        candidate = text
                    self._cache[text] = candidate
        return {text: self._cache.get(text, text) for text in texts}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _translate_memos(self, chunk: list[str]) -> dict[str, str]:
        """Translate a batch of memos, numbering every paragraph as its own line.

        Numbering each paragraph separately (rather than each memo) prevents the
        model from dropping everything after the first paragraph: a memo is only
        accepted when a translation is returned for every one of its paragraphs.
        """
        plan: list[tuple[int, int, str]] = []
        for memo_index, memo in enumerate(chunk):
            paragraphs = _split_paragraphs(memo) or [memo]
            for paragraph_index, paragraph in enumerate(paragraphs):
                plan.append((memo_index, paragraph_index, paragraph))

        paragraph_by_memo: dict[int, dict[int, str]] = defaultdict(dict)
        try:
            numbered = "\n".join(f"{index + 1}. {paragraph}" for index, (_, _, paragraph) in enumerate(plan))
            for line_no, value in (await self._request_translation_lines(numbered)).items():
                index = line_no - 1
                if not value:
                    continue
                if 0 <= index < len(plan):
                    memo_index, paragraph_index, _ = plan[index]
                    paragraph_by_memo[memo_index][paragraph_index] = value
        except Exception as exc:
            _LOGGER.warning("Activity translation failed for %d memo(s): %s", len(chunk), exc)

        result: dict[str, str] = {}
        for memo_index, memo in enumerate(chunk):
            paragraphs = _split_paragraphs(memo) or [memo]
            got = paragraph_by_memo.get(memo_index, {})
            if len(got) == len(paragraphs):
                result[memo] = "\n\n".join(got[paragraph_index] for paragraph_index in range(len(paragraphs)))
                continue
            retried = await self._translate_single(memo)
            if retried is None or self._incomplete(memo, paragraphs, retried):
                _LOGGER.warning(
                    "Incomplete translation for memo (%d chars, %d paragraphs); keeping original",
                    len(memo),
                    len(paragraphs),
                )
                result[memo] = memo
            else:
                result[memo] = retried
        return result

    @staticmethod
    def _incomplete(source: str, paragraphs: list[str], translation: str) -> bool:
        if translation == source or not translation:
            return False
        return len(_split_paragraphs(translation)) < len(paragraphs)

    async def _request_translation_lines(self, numbered: str) -> dict[int, str]:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Translate each numbered line into English. Reply with ONLY a JSON object - "
                        "no prose, no markdown - mapping each line number to its translation, e.g. "
                        f'{{"1": "first translation", "2": "second translation"}}.\n\n{numbered}'
                    ),
                },
            ],
            "temperature": 0.2,
        }
        content = await self._post_chat(payload)
        return self._parse_json_lines(content)

    async def _translate_single(self, text: str) -> str | None:
        """Retry one memo with an explicit completeness instruction.

        Returns ``None`` when the model cannot produce a usable translation so
        the caller can fall back to the original text.
        """
        if not text:
            return None
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Translate the ENTIRE memo below into English. Every paragraph must be "
                        "translated completely - do not summarize, condense, or omit any part. "
                        "Reply with only the translation.\n\n" + text
                    ),
                },
            ],
            "temperature": 0.2,
        }
        try:
            content = await self._post_chat(payload)
            return content.strip() or None
        except Exception as exc:
            _LOGGER.warning("Individual activity translation failed: %s", exc)
            return None

    async def _post_chat(self, payload: dict) -> str:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout))
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        async with self._session.post(url, json=payload, headers=headers) as response:
            response.raise_for_status()
            body = await response.json()
        return body["choices"][0]["message"]["content"]

    @classmethod
    def _parse_json_lines(cls, content: str) -> dict[int, str]:
        data = cls._extract_json(content)
        lines: dict[int, str] = {}
        for key, value in data.items():
            if str(key).isdigit() and int(key) >= 1:
                lines[int(key)] = str(value)
        return lines

    @staticmethod
    def _extract_json(content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise ValueError(f"Could not extract JSON from LLM response: {content!r}") from None
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"Could not parse LLM response: {content!r}") from exc


async def translate_activity_descriptions(translator: ActivityTranslator, events: list) -> None:
    from .mapping import PlannedEvent

    activities = [event for event in events if isinstance(event, PlannedEvent) and event.kind == "activity"]
    if not activities:
        return
    descriptions = [activity.payload.get("description", "") for activity in activities]
    translated = await translator.translate(descriptions)
    for activity in activities:
        description = activity.payload.get("description", "")
        if description in translated:
            activity.payload["description"] = translated[description]
