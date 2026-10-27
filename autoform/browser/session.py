"""The script's own browser profile (browser-profile/), and the CAS login for Moodle."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Error, Page, sync_playwright

from ..config import BROWSER_PROFILE_DIR

log = logging.getLogger("autoform")

CAS_USERNAME = "#username"
CAS_PASSWORD = "#password"
CAS_SUBMIT = '#fm1 input[name="submit"], #fm1 [type="submit"]'


class LoginFailed(Exception):
    """CAS did not let us in. Never retried automatically, so the account doesn't get locked."""


@contextmanager
def open_context(headless: bool = True, channel: str | None = None) -> Iterator[BrowserContext]:
    """channel "msedge" drives the installed Edge; None uses Playwright's own Chromium."""
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR),
            channel=channel,
            headless=headless,
            # A visible window keeps the size the user gives it; headless gets a normal desktop size.
            viewport={"width": 1280, "height": 900} if headless else None,
        )
        try:
            yield context
        finally:
            try:
                context.close()
            except Error:
                pass  # the user already closed the window


def _needs_login(page: Page, moodle_url: str, cas_url: str) -> bool:
    here = urlparse(page.url)
    if here.hostname == urlparse(cas_url).hostname:
        return True
    return here.hostname == urlparse(moodle_url).hostname and here.path.startswith("/login/")


def goto_moodle(page: Page, url: str, moodle_url: str, cas_url: str,
                get_credentials: Callable[[], tuple[str, str]]) -> None:
    """Open a Moodle page, logging in through CAS first if Moodle asks for it.

    get_credentials is only called when a login is actually needed.
    """
    page.goto(url)
    if not _needs_login(page, moodle_url, cas_url):
        return
    username, password = get_credentials()
    if urlparse(page.url).hostname != urlparse(cas_url).hostname:
        page.goto(cas_url)
    if page.locator(CAS_PASSWORD).count():
        log.info("logging in to CAS")
        page.fill(CAS_USERNAME, username)
        page.fill(CAS_PASSWORD, password)
        with page.expect_navigation():
            page.locator(CAS_SUBMIT).first.click()
    if urlparse(page.url).hostname == urlparse(cas_url).hostname:
        raise LoginFailed("CAS did not accept the saved username/password")
    page.goto(url)
    if _needs_login(page, moodle_url, cas_url):
        raise LoginFailed("still not logged in to Moodle after CAS")
