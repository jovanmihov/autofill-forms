"""state.json: which emails and activities were handled, so nothing is submitted twice."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .models import Analysis, Category

# A recorded email/activity in one of these states is never looked at again.
# "informed": an email without a form to answer, whose message went to your phone.
FINAL = {"submitted", "skipped", "needs_user", "failed", "informed"}
MAX_FAILURES = 3
KEEP_DAYS = 120
RETRY_SECONDS = 120  # how often a form that isn't open yet is looked at again


def _now(plus_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=plus_seconds)).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.emails: dict[str, dict] = data.get("emails", {})
        self.activities: dict[str, dict] = data.get("activities", {})
        self.login_blocked: str | None = data.get("login_blocked")

    def email_done(self, message_id: str) -> bool:
        return self.emails.get(message_id, {}).get("status") in FINAL

    def email_status(self, message_id: str) -> str | None:
        return self.emails.get(message_id, {}).get("status")

    def activity_done(self, url: str) -> bool:
        return self.activities.get(url, {}).get("status") in FINAL

    def email_summary(self, message_id: str) -> Analysis | None:
        """What the email was found to be about when it was first read (None if it wasn't yet)."""
        entry = self.emails.get(message_id, {})
        try:
            return Analysis(Category(entry["category"]), entry.get("message", ""), source=entry.get("source", "gemini"))
        except (KeyError, ValueError):
            return None

    def email_waiting(self, message_id: str) -> bool:
        """True while the email's form isn't open yet and isn't due for another look (no need to reopen it)."""
        entry = self.emails.get(message_id, {})
        return entry.get("status") == "retry" and bool(entry.get("url")) and not self.activity_due(entry["url"])

    def activity_due(self, url: str) -> bool:
        """False while a not-yet-open form is waiting for its next check."""
        entry = self.activities.get(url, {})
        return entry.get("status") != "retry" or entry.get("next_check", "") <= _now()

    def record_retry(self, url: str, **info) -> bool:
        """Remember to look at the form again later. Returns True the first time."""
        first = self.activities.get(url, {}).get("status") != "retry"
        self.record_activity(url, "retry", next_check=_now(RETRY_SECONDS), **info)
        return first

    def record_email(self, message_id: str, status: str, **info) -> None:
        entry = self.emails.setdefault(message_id, {})
        entry.update(info, status=status, at=_now())

    def record_activity(self, url: str, status: str, **info) -> None:
        entry = self.activities.setdefault(url, {})
        entry.update(info, status=status, at=_now())

    def record_failure(self, message_id: str, error: str, **info) -> bool:
        """Count a failed attempt. Returns True once it has failed too often and is given up."""
        entry = self.emails.setdefault(message_id, {})
        failures = entry.get("failures", 0) + 1
        entry.update(info, failures=failures, error=error, at=_now())
        entry["status"] = "failed" if failures >= MAX_FAILURES else "error"
        return failures >= MAX_FAILURES

    def block_login(self, reason: str) -> None:
        self.login_blocked = f"{_now()} {reason}"

    def unblock_login(self) -> None:
        self.login_blocked = None

    def save(self) -> None:
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).isoformat()
        for table in (self.emails, self.activities):
            for key in [k for k, v in table.items() if v.get("at", "") < cutoff]:
                del table[key]
        data = {"emails": self.emails, "activities": self.activities, "login_blocked": self.login_blocked}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
