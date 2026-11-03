"""Invite emails (S2-23): sent through Resend only once it is configured; until then the invites
wait, and the worker says why once. Sending from a real vault (on a mock Resend) runs in
apps/api/tests/test_people.py (`-m integration`)."""

import asyncio
import logging

import pytest

from zm_worker import invites


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM", "Z-Matrix <noreply@example.com>")
    monkeypatch.setenv("CLIENT_APP_BASE_URL", "https://app.example.com")


def test_without_resend_settings_nothing_is_sent_and_the_worker_says_so_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for var in invites.MAIL_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EMAIL_FROM", "Z-Matrix <noreply@example.com>")
    monkeypatch.setattr(invites, "_warned", False)
    with caplog.at_level(logging.WARNING, logger="zm_worker"):
        assert invites.client() is None
        assert invites.client() is None
    assert [r.getMessage() for r in caplog.records] == [
        "invite emails off, invites wait: set RESEND_API_KEY and CLIENT_APP_BASE_URL"
    ]


def test_the_resend_client_sends_the_key_as_a_bearer_token(configured: None) -> None:
    mail = invites.client()
    assert mail is not None
    assert mail.headers["authorization"] == "Bearer re_test_key"
    asyncio.run(mail.aclose())
