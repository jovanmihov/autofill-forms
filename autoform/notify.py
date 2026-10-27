"""Windows toast + a message to your phone (Discord, if a webhook is saved) + log line."""
from __future__ import annotations

import logging

log = logging.getLogger("autoform")


def notify(title: str, message: str, link: str | None = None) -> None:
    log.info("NOTIFY %s: %s%s", title, message, f" {link}" if link else "")
    try:
        from win11toast import notify as toast

        if link:
            toast(title, message, on_click=link)
        else:
            toast(title, message)
    except Exception:
        log.warning("could not show the toast notification", exc_info=True)
    send_to_phone(phone_text(title, message, link))


def phone_text(title: str, message: str, link: str | None = None) -> str:
    return f"**{title}**: {message}" + (f"\n{link}" if link else "")


def send_to_phone(text: str) -> bool:
    """Send text to the saved Discord webhook. A missing webhook or a failure is only logged."""
    from . import credentials, discord_webhook

    try:
        url = credentials.load_secret(credentials.DISCORD_WEBHOOK)
        if not url:
            return False
        discord_webhook.send(url, text)
        return True
    except Exception as e:
        log.warning("could not send the phone message: %s", e)
        return False
