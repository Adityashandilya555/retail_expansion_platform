"""Invite emails (S2-23): sent through Resend only once it is configured; until then the invites
wait, and the worker says why once. Sending from a real vault (on a mock Resend) runs in
apps/api/tests/test_people.py (`-m integration`)."""

import asyncio
import json
import logging
import uuid
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from zm_api import secrets
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


class FakeDb:
    """`ctx.db` for send(): every query answers `rows`."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def connect(self) -> "FakeDb":
        return self

    begin = connect

    async def __aenter__(self) -> "FakeDb":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, *_: object) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self.rows)


def test_an_invite_sealed_under_another_key_is_unsent_and_the_others_still_go(
    configured: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    old_key = Fernet(Fernet.generate_key())  # e.g. before SECRETS_ENCRYPTION_KEY was rotated
    rows = [
        SimpleNamespace(
            id=uuid.uuid4(),
            sealed=old_key.encrypt(json.dumps({"token": "pilot.old-secret"}).encode()),
            email="ana@pilot.test",
            name="Ana",
        ),
        SimpleNamespace(
            id=uuid.uuid4(),
            sealed=secrets.seal({"token": "pilot.new-secret"}),
            email="ben@pilot.test",
            name="Ben",
        ),
    ]
    who = SimpleNamespace(name="Pilot", slug="pilot", workspace_code="PILOT")
    sent: list[dict] = []

    def resend(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "e1"})

    async def go() -> Counter[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(resend)) as mail:
            return await invites.send(SimpleNamespace(db=FakeDb(rows), client=who), mail)

    with caplog.at_level(logging.WARNING, logger="zm_worker"):
        assert asyncio.run(go()) == Counter(unsent=1, sent=1)
    assert [m["to"] for m in sent] == [["ben@pilot.test"]]
    assert "pilot.new-secret" in sent[0]["text"]
    assert "invite token can't be unsealed" in caplog.text and "secret" not in caplog.text
