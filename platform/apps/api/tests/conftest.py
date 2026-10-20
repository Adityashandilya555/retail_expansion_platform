"""Shared unit-test fixtures: two fake registry rows (pilot-local, qsr-local) and their tokens."""

import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
from cryptography.fernet import Fernet

from zm_api import secrets
from zm_api.registry import Client

JWT_SECRET = "test-jwt-secret-0123456789abcdef0123456789abcdef"
PILOT_URL = "http://operaton-pilot.test:8080/engine-rest"
QSR_URL = "http://operaton-qsr.test:8080/engine-rest"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)


def client_row(slug: str, operaton_url: str, db_host: str, password: str, **changes: Any) -> Client:
    now = datetime(2026, 10, 19, tzinfo=UTC)
    row = Client(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, slug),
        slug=slug,
        name=slug.title(),
        workspace_code=slug.upper(),
        template_key="qsr",
        status="ready",
        supabase_ref=None,
        operaton_url=operaton_url,
        railway_service_id=None,
        bucket="files",
        secrets=secrets.seal(
            {
                "zm_api_dsn": f"postgresql://zm_api:db-{slug}@{db_host}:5432/postgres",
                "service_account_password": password,
            }
        ),
        created_by=None,
        created_at=now,
        updated_at=now,
    )
    return replace(row, **changes)


@pytest.fixture
def rows() -> dict[str, Client]:
    return {
        "pilot-local": client_row("pilot-local", PILOT_URL, "pilot-db", "pilot-pw"),
        "qsr-local": client_row("qsr-local", QSR_URL, "qsr-db", "qsr-pw"),
    }


def token(kind: str = "client", client: str | None = "pilot-local", **claims: Any) -> str:
    payload: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "kind": kind,
        "exp": int(time.time()) + 600,
    }
    if client is not None:
        payload["client"] = client
    payload.update(claims)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}
