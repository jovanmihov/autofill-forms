"""Messages to your phone through a Discord webhook: a channel in your own server, which the Discord
app pushes to your phone. The webhook URL is a secret (anyone with it can post to the channel), so
it lives only in Windows Credential Manager. This is the only module that talks to Discord.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

MAX_LENGTH = 2000  # Discord's limit for one message
SUPPRESS_EMBEDS = 1 << 2  # no link previews
_WEBHOOK_RE = re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+")
# Discord's firewall turns away urllib's default User-Agent.
_USER_AGENT = "autofill-forms/1.0"


class PhoneError(Exception):
    """The message didn't reach Discord. Never contains the webhook URL."""


def is_webhook_url(url: str) -> bool:
    return bool(_WEBHOOK_RE.fullmatch(url.strip()))


def payload(text: str) -> dict:
    """No @mentions (part of the text comes from emails) and no link previews."""
    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH - 1] + "…"
    return {"content": text, "allowed_mentions": {"parse": []}, "flags": SUPPRESS_EMBEDS}


def _retry_after(error: urllib.error.HTTPError) -> float:
    try:
        return float(json.loads(error.read() or b"{}").get("retry_after", 60))
    except (ValueError, AttributeError):
        return 60


def send(webhook_url: str, text: str, timeout: float = 10) -> None:
    if not is_webhook_url(webhook_url):
        raise PhoneError("the saved Discord webhook URL doesn't look right; run  py main.py set-discord-webhook")
    request = urllib.request.Request(
        webhook_url.strip() + "?wait=true",
        data=json.dumps(payload(text)).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout):
                return
        except urllib.error.HTTPError as e:
            wait = _retry_after(e) if e.code == 429 else None
            if wait is not None and wait <= 10 and attempt == 0:
                time.sleep(wait)  # rate limited: wait as long as Discord says, once
                continue
            raise PhoneError(f"Discord answered {e.code} {e.reason}") from None
        except OSError as e:  # URLError, timeouts
            raise PhoneError(f"can't reach Discord ({type(e).__name__})") from None
