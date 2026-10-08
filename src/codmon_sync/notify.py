from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)

OK_TITLE = "# 👍 sync ok"
ERROR_TITLE = "# sync error"


class AppriseNotifier:
    def __init__(self, url: str | None) -> None:
        self.url = url

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def send(self, *, title: str, body: str, message_type: str = "success") -> bool:
        if not self.url:
            return False
        payload = {"title": title, "body": body, "type": message_type}
        try:
            async with (
                aiohttp.ClientSession(timeout=_TIMEOUT) as session,
                session.post(self.url, json=payload) as response,
            ):
                response.raise_for_status()
                _LOGGER.info("Apprise notification sent: %s", title)
                return True
        except Exception as exc:
            _LOGGER.warning("Apprise notification failed (%s): %s", title, exc)
            return False
