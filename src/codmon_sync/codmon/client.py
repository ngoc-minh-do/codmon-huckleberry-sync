from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Response, async_playwright

from .models import CodmonPost, DailyReport

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://parents.codmon.com"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

_MAX_NETWORK_ENTRIES = 200
_MAX_NETWORK_BYTES = 12 * 1024 * 1024

POST_ITEM_SELECTORS = [
    "#timeline_page div.timeline_content > div",
    "div.timeline_content > div",
    ".timeline_post",
]

TIMELINE_FILTER_BUTTONS = [
    "div.timelineHeader__filter",
    ".timelineHeader__wrapper [class*='filter']",
]

DETAIL_ROOT_SELECTORS = [
    "#timeline_page ons-page.selectable-container.page .page__content",
    "ons-page.selectable-container.page .page__content",
    ".block__white--padding.block__white--noborder",
]

BACK_BUTTON_SELECTORS = [
    "ons-back-button",
    ".toolbar__left ons-back-button",
]


class CodmonError(RuntimeError):
    pass


class CodmonClient:
    def __init__(
        self,
        email: str,
        password: str,
        *,
        timezone: str,
        headless: bool = True,
        record_har_path: str | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.timezone = timezone
        self.headless = headless
        self.record_har_path = record_har_path
        self._responses: list[dict] = []
        self._response_bytes = 0
        self._requests: list[dict] = []
        self.page: Page | None = None
        self._playwright_handle = None
        self._browser = None
        self._context = None

    async def start(self) -> CodmonClient:
        self._playwright_handle = await async_playwright().start()
        self._browser = await self._playwright_handle.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": 390, "height": 844},
            timezone_id=self.timezone,
            locale="ja-JP",
            user_agent=MOBILE_UA,
            record_har_path=self.record_har_path,
        )
        self.page = await self._context.new_page()
        self.page.on("response", lambda response: asyncio.create_task(self._capture_response(response)))
        self.page.on("request", lambda request: self._capture_request(request))
        return self

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright_handle:
            await self._playwright_handle.stop()

    async def _capture_response(self, response: Response) -> None:
        if len(self._responses) >= _MAX_NETWORK_ENTRIES or self._response_bytes >= _MAX_NETWORK_BYTES:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
        if not re.search(r"api|graphql|parents\.codmon", response.url, re.IGNORECASE):
            return
        try:
            body: Any = await response.text()
        except Exception:
            return
        self._response_bytes += len(body)
        self._responses.append({"url": response.url, "status": response.status, "body": body})

    def _capture_request(self, request) -> None:
        if not re.search(r"api|graphql|parents\.codmon", request.url, re.IGNORECASE):
            return
        if request.url == BASE_URL:
            return
        headers = request.headers
        interesting = {
            name: value
            for name, value in headers.items()
            if name.lower()
            in {
                "authorization",
                "x-access-token",
                "x-auth-token",
                "x-csrf-token",
                "cookie",
                "content-type",
                "x-app-version",
                "x-requested-with",
            }
        }
        self._requests.append(
            {
                "method": request.method,
                "url": request.url,
                "headers": interesting,
                "post_data": request.post_data,
            }
        )

    async def _goto(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2500)

    async def login(self) -> None:
        assert self.page is not None
        page = self.page
        await self._goto(BASE_URL)

        already = page.get_by_text("アカウントをお持ちの方", exact=False)
        if await already.count():
            await already.first.click()
            await page.wait_for_timeout(1500)

        email_input = page.locator('input[type="email"]')
        if await email_input.count() == 0:
            email_input = page.locator('input[autocomplete="email"]')
        if await email_input.count() == 0:
            raise CodmonError("Could not locate the Codmon login email input")
        await email_input.first.fill(self.email)

        password_input = page.locator('input[type="password"]')
        if await password_input.count() == 0:
            raise CodmonError("Could not locate the Codmon login password input")
        await password_input.first.fill(self.password)

        submit = page.get_by_role("button", name=re.compile(r"ログイン"))
        if await submit.count() == 0:
            submit = page.locator("ons-button")
        if await submit.count() == 0:
            raise CodmonError("Could not locate the Codmon login submit button")
        await submit.first.click()

        await page.wait_for_timeout(6000)
        await page.wait_for_load_state("load", timeout=30000)

        if await page.locator('input[type="password"]').count():
            raise CodmonError("Codmon login failed: still on the login form")
        _LOGGER.info("Codmon login succeeded (URL=%s)", page.url)

    async def _open_timeline(self) -> None:
        assert self.page is not None
        page = self.page

        filter_button = None
        for selector in TIMELINE_FILTER_BUTTONS:
            locator = page.locator(selector)
            if await locator.count():
                filter_button = locator.first
                break
        if filter_button is None:
            menu = page.get_by_text("連絡帳", exact=False)
            if await menu.count():
                href = await menu.first.get_attribute("href")
                if href:
                    await self._goto(f"{BASE_URL}{href if href.startswith('/') else '/' + href}")
                    filter_button = None
                    for selector in TIMELINE_FILTER_BUTTONS:
                        locator = page.locator(selector)
                        if await locator.count():
                            filter_button = locator.first
                            break
            if filter_button is None:
                candidates = [
                    "連絡帳",
                    "れんらくちょう",
                ]
                for text in candidates:
                    el = page.get_by_text(text, exact=False)
                    if await el.count():
                        await el.first.click()
                        await page.wait_for_timeout(2000)
                        break
                for selector in TIMELINE_FILTER_BUTTONS:
                    locator = page.locator(selector)
                    if await locator.count():
                        filter_button = locator.first
                        break
        if filter_button is None:
            return

        await filter_button.click()
        await page.wait_for_timeout(1500)

        check = page.get_by_text("連絡帳", exact=True)
        if await check.count():
            try:
                label = await check.first.locator("xpath=ancestor::label").count() and check.first.locator(
                    "xpath=ancestor::label"
                )
                await label.first.click()
            except Exception:
                await check.first.click()
        await page.wait_for_timeout(800)

        decide = page.get_by_text("決定", exact=True)
        if await decide.count():
            await decide.first.click()
        elif page.get_by_text("検索", exact=True):
            search = page.get_by_text("検索", exact=True)
            if await search.count():
                await search.first.click()
        await page.wait_for_timeout(2500)

    async def _timeline_items(self) -> list:
        assert self.page is not None
        for selector in POST_ITEM_SELECTORS:
            locator = self.page.locator(selector)
            count = await locator.count()
            if count:
                items = [locator.nth(i) for i in range(count)]
                return [item for item in items if item]
        return []

    async def _open_post_details(self, item) -> str:
        assert self.page is not None
        page = self.page
        await item.click()
        await page.wait_for_timeout(2500)
        back_count = await self._try_capture_detail()
        await self._go_back()
        return back_count

    async def _try_capture_detail(self) -> str:
        assert self.page is not None
        page = self.page
        for selector in DETAIL_ROOT_SELECTORS:
            locator = page.locator(selector)
            if await locator.count():
                try:
                    return await locator.first.inner_text()
                except Exception:
                    continue
        return await page.locator("body").inner_text()

    async def _go_back(self) -> None:
        assert self.page is not None
        page = self.page
        back = page.locator("ons-back-button")
        if await back.count():
            await back.first.click()
        else:
            await page.go_back()
        await page.wait_for_timeout(1800)

    async def introspect(self, output_dir: Path, target: date) -> DailyReport:
        assert self.page is not None
        await self.login()
        await self._dump_step(output_dir / "login_after")
        await self._dump_storage(output_dir / "login_storage")

        await self._open_timeline()
        await self._dump_step(output_dir / "timeline")
        await self._dump_storage(output_dir / "timeline_storage")

        posts = await self._collect_posts(output_dir, target)
        network = list(self._responses)
        requests = list(self._requests)
        await self._write_network(output_dir, network)
        await self._write_requests(output_dir, requests)

        return DailyReport(date=target, posts=posts, network=network)

    async def _dump_storage(self, prefix: Path) -> None:
        assert self.page is not None and self._context is not None
        payload: dict[str, Any] = {}
        try:
            local = await self.page.evaluate(
                """() => {
                    const out = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        out[key] = localStorage.getItem(key);
                    }
                    return out;
                }"""
            )
            payload["localStorage"] = local
        except Exception as exc:
            _LOGGER.warning("localStorage dump failed (%s): %s", prefix, exc)
        try:
            session = await self.page.evaluate(
                """() => {
                    const out = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        out[key] = sessionStorage.getItem(key);
                    }
                    return out;
                }"""
            )
            payload["sessionStorage"] = session
        except Exception as exc:
            _LOGGER.warning("sessionStorage dump failed (%s): %s", prefix, exc)
        try:
            payload["cookies"] = await self._context.cookies()
        except Exception as exc:
            _LOGGER.warning("cookie dump failed (%s): %s", prefix, exc)
        await self._write_text(
            prefix.with_suffix(".json"),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    async def fetch_daily_report(self, target: date) -> DailyReport:
        assert self.page is not None
        await self.login()
        await self._open_timeline()
        posts = await self._collect_posts(None, target)
        return DailyReport(date=target, posts=posts, network=list(self._responses))

    async def _collect_posts(self, output_dir: Path | None, target: date) -> list[CodmonPost]:
        items = await self._timeline_items()
        posts: list[CodmonPost] = []
        for index, item in enumerate(items):
            try:
                text = await self._open_post_details(item)
            except Exception as exc:
                _LOGGER.warning("Could not open timeline post %d: %s", index, exc)
                continue
            post = CodmonPost(
                post_id=f"post-{index}",
                body_text=text,
            )
            if output_dir is not None:
                await self._write_text(output_dir / f"post_{index:02d}.txt", text)
            posts.append(post)
            if output_dir is not None and index >= 4:
                break
        return posts

    async def _dump_step(self, prefix: Path) -> None:
        assert self.page is not None
        prefix.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = await self.page.locator("body").inner_text()
            await self._write_text(prefix.with_suffix(".txt"), text)
        except Exception as exc:
            _LOGGER.warning("inner_text dump failed (%s): %s", prefix, exc)
        try:
            html = await self.page.content()
            await self._write_text(prefix.with_suffix(".html"), html)
        except Exception as exc:
            _LOGGER.warning("html dump failed (%s): %s", prefix, exc)
        try:
            await self.page.screenshot(path=str(prefix.with_suffix(".png")), full_page=False)
        except Exception as exc:
            _LOGGER.warning("screenshot dump failed (%s): %s", prefix, exc)

    async def _write_requests(self, output_dir: Path, requests: list[dict]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        await self._write_text(
            output_dir / "requests.json",
            json.dumps(requests, ensure_ascii=False, indent=2),
        )

    async def _write_network(self, output_dir: Path, responses: list[dict]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        await self._write_text(
            output_dir / "network_summary.json",
            json.dumps(
                [{"url": r["url"], "status": r["status"]} for r in responses],
                ensure_ascii=False,
                indent=2,
            ),
        )
        for index, response in enumerate(responses):
            url = response["url"]
            safe_name = re.sub(
                r"[^A-Za-z0-9._-]+", "_", url.split("?")[0].rstrip("/").split("/")[-1] or f"resp-{index}"
            )
            await self._write_text(
                output_dir / f"net_{index:03d}_{safe_name}.json",
                response["body"],
            )

    @staticmethod
    async def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
