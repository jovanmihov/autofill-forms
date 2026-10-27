"""Development helper: the user clicks around in the script's browser, and every page they
stop on is saved (HTML, screenshot, accessibility tree) so selectors can be written from it.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Error, Page

from .session import open_context

SETTLE_SECONDS = 4  # a page must keep the same URL this long before it is saved

# A small badge in the corner so the user can see the page was saved and move on.
_BADGE_JS = """text => {
  const badge = document.createElement('div');
  badge.textContent = text;
  badge.style.cssText = 'position:fixed;top:12px;right:12px;z-index:2147483647;padding:8px 14px;'
    + 'background:#1a7f37;color:#fff;font:600 15px system-ui,sans-serif;border-radius:8px;pointer-events:none';
  document.documentElement.appendChild(badge);
  setTimeout(() => badge.remove(), 2500);
}"""


def _short(url: str) -> str:
    parts = urlparse(url)  # no query string: login redirects carry tokens there
    return f"{parts.hostname or ''}{parts.path}"


def _name(url: str, number: int) -> str:
    parts = urlparse(url)
    path = re.sub(r"\W+", "-", parts.path).strip("-")[:40]
    return f"{number:02d}-{parts.hostname or 'page'}{'-' + path if path else ''}"


def _is_login_page(page: Page) -> bool:
    return page.locator("input[type=password]:visible").count() > 0


def _save(page: Page, out_dir: Path, number: int) -> str:
    name = _name(page.url, number)
    (out_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
    (out_dir / f"{name}.aria.yaml").write_text(f"# {page.url}\n" + page.locator("body").aria_snapshot(), encoding="utf-8")
    page.screenshot(path=str(out_dir / f"{name}.png"))
    return name


def _watch(context: BrowserContext, out_dir: Path, deadline: float | None) -> int:
    count = 0
    seen: dict[Page, tuple[str, float]] = {}  # url and when it was first seen
    saved: dict[Page, str] = {}
    while context.pages and (deadline is None or time.monotonic() < deadline):
        for page in list(context.pages):
            try:
                url = page.evaluate("location.href")  # a round trip, so Playwright also notices closed tabs
            except Error:
                continue
            if url.startswith(("about:", "chrome:", "edge:")):
                continue
            if seen.get(page, ("", 0))[0] != url:
                seen[page] = (url, time.monotonic())
                print(f"page: {_short(url)}", flush=True)
                continue
            if saved.get(page) == url or time.monotonic() - seen[page][1] < SETTLE_SECONDS:
                continue
            try:
                if _is_login_page(page):
                    print("  login page, not saved", flush=True)
                else:
                    number = count + 1
                    print(f"  saved {_save(page, out_dir, number)}", flush=True)
                    count = number
                    page.evaluate(_BADGE_JS, "Saved ✓")
                saved[page] = url
            except Error as e:
                print(f"  could not save yet: {str(e).splitlines()[0]}", flush=True)
        time.sleep(1)
    return count


def browse(start_url: str, out_dir: Path, channel: str | None = None,
           headless: bool = False, max_seconds: float | None = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max_seconds if max_seconds else None
    count = 0
    with open_context(headless=headless, channel=channel) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(start_url)
        try:
            count = _watch(context, out_dir, deadline)
        except Error as e:
            print(f"browser closed: {str(e).splitlines()[0]}", flush=True)
    return count
