from __future__ import annotations

import json
import logging
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
                try:
                    translated = await self._translate_chunk(chunk)
                except Exception as exc:
                    _LOGGER.warning("Activity translation failed for %d memo(s): %s", len(chunk), exc)
                    translated = {text: text for text in chunk}
                self._cache.update(translated)
        return {text: self._cache.get(text, text) for text in texts}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _translate_chunk(self, chunk: list[str]) -> dict[str, str]:
        numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(chunk))
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Translate each numbered line into English. Reply with ONLY a JSON object - "
                        f"no prose, no markdown - mapping each line number to its translation, e.g. "
                        f'{{"1": "first translation", "2": "second translation"}}.\n\n{numbered}'
                    ),
                },
            ],
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"

        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout))
        async with self._session.post(url, json=payload, headers=headers) as response:
            response.raise_for_status()
            body = await response.json()
        content = body["choices"][0]["message"]["content"]
        return self._parse_json(content, chunk)

    @staticmethod
    def _parse_json(content: str, chunk: list[str]) -> dict[str, str]:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise ValueError(f"Could not extract JSON from LLM response: {content!r}") from None
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"Could not parse LLM response: {content!r}") from exc
        mapping: dict[str, str] = {}
        for index, value in data.items():
            if not str(index).isdigit() or int(index) < 1 or int(index) > len(chunk):
                continue
            mapping[chunk[int(index) - 1]] = str(value)
        return mapping


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
