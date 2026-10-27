"""Secrets in Windows Credential Manager (keyring), never in a file: the CAS username + password,
the Gemini API key and the Discord webhook URL.
"""
from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "autofill-forms/cas.finki.ukim.mk"
_USERNAME_KEY = "__username__"

GEMINI_KEY = "gemini-api-key"
DISCORD_WEBHOOK = "discord-webhook"


def save(username: str, password: str) -> None:
    old = keyring.get_password(SERVICE, _USERNAME_KEY)
    if old and old != username:
        try:
            keyring.delete_password(SERVICE, old)
        except PasswordDeleteError:
            pass
    keyring.set_password(SERVICE, _USERNAME_KEY, username)
    keyring.set_password(SERVICE, username, password)


def load() -> tuple[str, str] | None:
    username = keyring.get_password(SERVICE, _USERNAME_KEY)
    password = keyring.get_password(SERVICE, username) if username else None
    return (username, password) if username and password else None


def save_secret(name: str, value: str) -> None:
    keyring.set_password(f"autofill-forms/{name}", name, value)


def load_secret(name: str) -> str | None:
    return keyring.get_password(f"autofill-forms/{name}", name) or None
