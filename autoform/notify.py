"""Windows toast + log line."""
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
