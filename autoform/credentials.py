"""CAS username + password in Windows Credential Manager (keyring). Never in a file."""
from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "autofill-forms/cas.finki.ukim.mk"
_USERNAME_KEY = "__username__"


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
