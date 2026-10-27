"""No test reaches the network or the real Credential Manager: Gemini and Discord are faked."""
import ipaddress
import socket

import keyring
import pytest

_connect = socket.socket.connect


def _local_only(self, address):
    host = address[0] if isinstance(address, tuple) else str(address)
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if not local:
        raise RuntimeError(f"tests must not use the network (tried to connect to {host})")
    return _connect(self, address)


def _no_keyring(*args, **kwargs):
    raise RuntimeError("tests must not touch Windows Credential Manager")


@pytest.fixture(autouse=True)
def no_network_or_secrets(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _local_only)
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, _no_keyring)
