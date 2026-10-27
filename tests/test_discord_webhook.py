"""Discord is faked: urlopen is replaced, so nothing is sent."""
import io
import json
import urllib.error

import pytest

from autoform import discord_webhook, notify
from autoform.discord_webhook import PhoneError, is_webhook_url, payload, send

URL = "https://discord.com/api/webhooks/123456789/secret-Token_value"


@pytest.mark.parametrize("url, ok", [
    (URL, True),
    ("https://discordapp.com/api/webhooks/1/abc", True),
    ("https://canary.discord.com/api/v10/webhooks/1/abc", True),
    ("http://discord.com/api/webhooks/1/abc", False),
    ("https://discord.com.evil.example/api/webhooks/1/abc", False),
    ("https://discord.com/api/webhooks/1/abc?x=1", False),
    ("https://example.com/hook", False),
])
def test_webhook_urls(url, ok):
    assert is_webhook_url(url) is ok


def test_payload_has_no_mentions_or_previews_and_fits():
    data = payload("@everyone free pizza " + "x" * 3000)
    assert data["allowed_mentions"] == {"parse": []}
    assert data["flags"] == discord_webhook.SUPPRESS_EMBEDS
    assert len(data["content"]) == discord_webhook.MAX_LENGTH


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def capture(monkeypatch, *outcomes):
    """urlopen that records requests and raises/returns outcomes in order."""
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        outcome = outcomes[len(requests) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse()

    monkeypatch.setattr(discord_webhook.urllib.request, "urlopen", urlopen)
    return requests


def http_error(code, body=b""):
    return urllib.error.HTTPError(URL, code, "error", {}, io.BytesIO(body))


def test_send(monkeypatch):
    requests = capture(monkeypatch, None)
    send(URL, "Lab: chose Wed 12-14")
    request = requests[0]
    assert request.full_url == URL + "?wait=true"
    assert request.get_method() == "POST"
    assert request.get_header("User-agent") == "autofill-forms/1.0"
    assert json.loads(request.data)["content"] == "Lab: chose Wed 12-14"


def test_rate_limit_waits_once(monkeypatch):
    slept = []
    monkeypatch.setattr(discord_webhook.time, "sleep", slept.append)
    requests = capture(monkeypatch, http_error(429, b'{"retry_after": 1.5}'), None)
    send(URL, "hi")
    assert slept == [1.5] and len(requests) == 2


def test_errors_never_show_the_webhook_token(monkeypatch):
    capture(monkeypatch, http_error(404))
    with pytest.raises(PhoneError) as caught:
        send(URL, "hi")
    assert "secret" not in str(caught.value) and "404" in str(caught.value)

    capture(monkeypatch, urllib.error.URLError("no route to " + URL))
    with pytest.raises(PhoneError) as caught:
        send(URL, "hi")
    assert "secret" not in str(caught.value)


def test_bad_saved_url_is_refused_without_sending(monkeypatch):
    requests = capture(monkeypatch)
    with pytest.raises(PhoneError):
        send("https://example.com/hook", "hi")
    assert requests == []


# --- notify.send_to_phone ---------------------------------------------------

def test_phone_text():
    assert notify.phone_text("Lab", "Chose Wed", "https://courses.example.edu/x") == "**Lab**: Chose Wed\nhttps://courses.example.edu/x"


def test_send_to_phone_without_a_webhook_does_nothing(monkeypatch):
    monkeypatch.setattr("autoform.credentials.load_secret", lambda name: None)
    monkeypatch.setattr(discord_webhook, "send", lambda *a: pytest.fail("sent without a webhook"))
    assert notify.send_to_phone("hi") is False


def test_send_to_phone_failure_is_only_logged(monkeypatch):
    def fail(url, text):
        raise PhoneError("Discord answered 500")

    monkeypatch.setattr("autoform.credentials.load_secret", lambda name: URL)
    monkeypatch.setattr(discord_webhook, "send", fail)
    assert notify.send_to_phone("hi") is False

    sent = []
    monkeypatch.setattr(discord_webhook, "send", lambda url, text: sent.append((url, text)))
    assert notify.send_to_phone("hi") is True
    assert sent == [(URL, "hi")]
