"""Pick the Moodle activity to open from an email's links. Pure: no I/O."""
from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse

from .classifier import mentions

ACTIVITY_MODULES = ("choice", "feedback", "questionnaire", "groupselect", "scheduler")
_ACTIVITY_RE = re.compile(
    r"/mod/(?P<mod>" + "|".join(ACTIVITY_MODULES) + r")/view\.php\?(?:[^#\s]*&)?id=(?P<id>\d+)"
)
_FORUM_RE = re.compile(r"/mod/forum/discuss\.php\?(?:[^#\s]*&)?d=(?P<id>\d+)")


def unwrap_safelink(url: str) -> str:
    """Microsoft Defender may wrap links as https://*.safelinks.protection.outlook.com/?url=<real>."""
    parts = urlparse(url)
    if (parts.hostname or "").endswith("safelinks.protection.outlook.com"):
        return parse_qs(parts.query).get("url", [url])[0]
    return url


def _on_site(url: str, base_url: str) -> bool:
    return urlparse(url).hostname == urlparse(base_url).hostname


def activity_links(links: Iterable[tuple[str, str]], base_url: str) -> dict[str, list[str]]:
    """Normalized activity URL -> the texts of the links pointing at it, in order."""
    found: dict[str, list[str]] = {}
    for url, text in links:
        url = unwrap_safelink(url)
        if _on_site(url, base_url) and (m := _ACTIVITY_RE.search(url)):
            found.setdefault(f"{base_url}/mod/{m['mod']}/view.php?id={m['id']}", []).append(text)
    return found


def forum_links(links: Iterable[tuple[str, str]], base_url: str) -> list[str]:
    found = []
    for url, _ in links:
        url = unwrap_safelink(url)
        if _on_site(url, base_url) and (m := _FORUM_RE.search(url)):
            url = f"{base_url}/mod/forum/discuss.php?d={m['id']}"
            if url not in found:
                found.append(url)
    return found


def pick_target(links: Iterable[tuple[str, str]], base_url: str, keywords: Iterable[str] = ()) -> tuple[str, str | None]:
    """("activity", url), ("forum", url), ("several", None) or ("none", None).

    With several activity links, the one whose link text has a form keyword wins
    ("анкета за термин за лабораториски вежби" over "анкета за избор на проект").
    """
    links = list(links)
    activities = activity_links(links, base_url)
    keywords = tuple(keywords)
    if len(activities) > 1 and keywords:
        named = {url: texts for url, texts in activities.items() if mentions(" ".join(texts), keywords, prefix=True)}
        activities = named or activities
    if len(activities) == 1:
        return "activity", next(iter(activities))
    if activities:
        return "several", None
    forums = forum_links(links, base_url)
    if forums:
        return "forum", forums[0]
    return "none", None


def is_choice(url: str) -> bool:
    return "/mod/choice/" in url
