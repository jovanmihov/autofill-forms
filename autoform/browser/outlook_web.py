"""Outlook on the web in the script's own browser. Selectors use attributes, not visible
labels, so they keep working whatever language Outlook is set to.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote, urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..config import SetupError
from ..models import Email

log = logging.getLogger("autoform")

LIST = '[role="listbox"]'
ITEM = '[role="listbox"] [role="option"][data-convid]'
TAB = 'button[role="tab"][value="{}"]'  # the Focused / Other tabs
BODY = 'div[id^="UniqueMessageBody"]'
SUBJECT = '[role="heading"][id$="_SUBJECT"][title]'
# A row's first titled button is Outlook's read/unread toggle ("Mark as read" / "Mark as unread").
# Only whether its title changes is used, so the language of the titles doesn't matter.
TOGGLE = "button[title]"
MARK_READ_WAIT_MS = 2000  # how long Outlook gets to mark an opened email as read

_READ_ITEMS_JS = """() => [...document.querySelectorAll('[role="listbox"] [role="option"][data-convid]')].map(o => ({
  convId: o.getAttribute('data-convid'),
  label: o.getAttribute('aria-label') || '',
  titles: [...o.querySelectorAll('span[title]')].map(s => s.getAttribute('title')),
}))"""
_BODY_LINKS_JS = """bodies => bodies.flatMap(b => [...b.querySelectorAll('a[href]')].map(a => [a.href, a.innerText.trim()]))"""

_TOGGLE_JS = """([convId, before, changed]) => {
  const row = document.querySelector(`[role="listbox"] [role="option"][data-convid="${CSS.escape(convId)}"]`);
  const toggle = row && row.querySelector('button[title]');
  return !!toggle && (toggle.title !== before) === changed;
}"""

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)?", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"[^\s@<>]+@([^\s@<>]+\.[^\s@<>]+)")


class OutlookLoginNeeded(SetupError):
    """Outlook shows Microsoft's login page: the user has to log in again (py main.py browse)."""


@dataclass(frozen=True)
class ListedEmail:
    conv_id: str
    label: str  # sender, subject, time and preview, as Outlook reads them out
    received: datetime | None
    tab: str | None = None  # "focused" / "other", or None without Focused Inbox
    sender: str = ""


def is_course_sender(address: str, domain_prefix: str) -> bool:
    """True for any address on a domain starting with domain_prefix ("x@courses.example.edu" for "courses.")."""
    m = _ADDRESS_RE.fullmatch(address.strip())
    return bool(m and domain_prefix and m.group(1).casefold().startswith(domain_prefix.casefold()))


def parse_list_date(text: str) -> datetime | None:
    """Outlook's date tooltip: "Mon 10/26/2026 10:20 AM" (US) or "26/10/2026 10:20" (24-hour locales)."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    first, second, year, hour, minute, ampm = m.groups()
    if ampm:
        month, day = int(first), int(second)
        hour = int(hour) % 12 + (12 if ampm.upper() == "PM" else 0)
    else:
        day, month, hour = int(first), int(second), int(hour)
    try:
        return datetime(int(year), month, day, hour, int(minute))
    except ValueError:
        return None


def open_inbox(page: Page, url: str, timeout_ms: int) -> None:
    page.goto(url)
    try:
        page.wait_for_selector(LIST, timeout=timeout_ms)
    except PlaywrightTimeout:
        host = urlparse(page.url).hostname or ""
        if not host.startswith("outlook."):
            raise OutlookLoginNeeded(f"Outlook wants a login ({host}): run  py main.py browse  and log in") from None
        raise


def list_course_emails(page: Page, domain_prefix: str, since: datetime | None = None,
                       max_scrolls: int = 0) -> list[ListedEmail]:
    """Emails from any address @<domain_prefix>* in the Inbox (Focused and Other), newest first.

    Outlook only draws the rows on screen, so with max_scrolls > 0 the list is
    scrolled down until rows older than since show up or the list ends.
    """
    found: dict[str, ListedEmail] = {}
    tabs = [value for value in ("focused", "other") if page.locator(TAB.format(value)).count()] or [None]
    for tab in tabs:
        _select_tab(page, tab)
        _scroll_to_top(page)
        seen: set[str] = set()
        for scroll in range(max_scrolls + 1):
            rows = page.evaluate(_READ_ITEMS_JS)
            new = [r for r in rows if r["convId"] not in seen]
            seen.update(r["convId"] for r in rows)
            oldest = None
            for raw in new:
                titles = [t or "" for t in raw["titles"]]
                received = next((d for d in map(parse_list_date, titles) if d), None)
                oldest = min(filter(None, (oldest, received)), default=None)
                sender = next((t for t in titles if is_course_sender(t, domain_prefix)), None)
                if sender:
                    found.setdefault(raw["convId"], ListedEmail(raw["convId"], raw["label"], received, tab, sender))
            if scroll == max_scrolls or (since and oldest and oldest < since) or not _scroll(page):
                break
    emails = [e for e in found.values() if not since or not e.received or e.received >= since]
    return sorted(emails, key=lambda e: e.received or datetime.min, reverse=True)


def _select_tab(page: Page, tab: str | None) -> None:
    if tab and page.locator(f'{TAB.format(tab)}[aria-selected="true"]').count() == 0:
        page.locator(TAB.format(tab)).click()
        page.wait_for_selector(f'{TAB.format(tab)}[aria-selected="true"]')
        page.wait_for_timeout(1500)  # the list redraws after the switch


# Scroll the message list's scroll container. "down" moves 3/4 of a screen, so no row is
# skipped: Outlook only draws the rows near the visible part of the list.
_SCROLL_JS = """where => {
  // Outlook's list is a React Virtuoso list: the scrolling element sits inside the listbox.
  const scrolls = el => /(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight;
  let el = document.querySelector('[role="listbox"] [data-virtuoso-scroller]');
  if (!el || !scrolls(el)) {
    el = document.querySelector('[role="listbox"]');
    while (el && !scrolls(el)) el = el.parentElement;
  }
  if (!el) return false;
  const before = el.scrollTop;
  el.scrollTop = where === 'top' ? 0 : before + el.clientHeight * 0.75;
  return el.scrollTop !== before;
}"""


def _scroll(page: Page) -> bool:
    moved = page.evaluate(_SCROLL_JS, "down")
    if moved:
        page.wait_for_timeout(800)
    return moved


def _scroll_to_top(page: Page) -> None:
    if page.evaluate(_SCROLL_JS, "top"):
        page.wait_for_timeout(800)


def _show_item(page: Page, item: ListedEmail, max_scrolls: int = 40):
    """The row for item, scrolling to it if Outlook has stopped drawing it."""
    _select_tab(page, item.tab)
    row = page.locator(f'{ITEM}[data-convid="{item.conv_id}"]').first
    if row.count():
        return row
    _scroll_to_top(page)
    for _ in range(max_scrolls):
        if row.count():
            return row
        if not _scroll(page):
            break
    raise LookupError(f"email {item.conv_id} is no longer in the Inbox list")


def read_email(page: Page, item: ListedEmail, timeout_ms: int, keep_unread: bool = False) -> Email:
    """Open the email in the reading pane and read its text and links.

    Outlook marks an opened email as read. With keep_unread, an email that was unread
    before is marked unread again right after it has been read.
    """
    row = _show_item(page, item)
    toggle = row.locator(TOGGLE).first
    before = toggle.get_attribute("title") if toggle.count() else None
    row.click()
    page.wait_for_url(lambda url: item.conv_id in unquote(url), timeout=timeout_ms)
    page.wait_for_selector(BODY, timeout=timeout_ms)
    page.wait_for_timeout(500)  # the body is filled in a moment after it appears
    bodies = page.locator(BODY)
    subject = page.locator(SUBJECT).first.get_attribute("title") or ""
    text = "\n".join(bodies.all_inner_texts())
    links = tuple((href, label) for href, label in bodies.evaluate_all(_BODY_LINKS_JS))
    log.debug("read email %r: %d link(s)", subject, len(links))
    if keep_unread and before is not None:
        _restore_unread(page, item, before)
    return Email(message_id=item.conv_id, subject=subject, body=text, received=item.received, links=links)


def _toggle_is(page: Page, item: ListedEmail, before: str, changed: bool, timeout_ms: int) -> bool:
    try:
        page.wait_for_function(_TOGGLE_JS, arg=[item.conv_id, before, changed], timeout=timeout_ms)
        return True
    except PlaywrightTimeout:
        return False


def _restore_unread(page: Page, item: ListedEmail, before: str) -> None:
    """If opening the email flipped its read/unread toggle, flip it back."""
    if not _toggle_is(page, item, before, changed=True, timeout_ms=MARK_READ_WAIT_MS):
        return  # it was read already
    toggle = _show_item(page, item).locator(TOGGLE).first
    toggle.dispatch_event("click")
    if not _toggle_is(page, item, before, changed=False, timeout_ms=3000):
        _show_item(page, item).hover()  # the toggle may only react to a real click on the hovered row
        toggle.click(force=True)
        if not _toggle_is(page, item, before, changed=False, timeout_ms=3000):
            log.warning("could not mark email %s unread again", item.conv_id)
            return
    log.debug("marked email %s unread again", item.conv_id)
