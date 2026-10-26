"""`zm-worker provision-local` (#31) without the stack: the CLI's checks, the DSN it registers,
and the order of its steps. The isolation suite runs it for real (`pytest -m isolation`)."""

from typing import Any

import pytest
from cryptography.fernet import Fernet

import zm_worker.__main__ as worker
from zm_api import secrets
from zm_worker.provision import local


def test_the_api_dsn_is_the_vault_as_zm_api() -> None:
    dsn = "postgresql+asyncpg://postgres:postgres@localhost:5434/postgres"
    assert local.api_dsn(dsn, "pw") == "postgresql://zm_api:pw@localhost:5434/postgres"


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, value in {
        "ZM_API_DB_PASSWORD": "api",
        "ZM_ENGINE_DB_PASSWORD": "engine",
        "ZM_ADMIN_USER_PASSWORD": "service",
        "PLATFORM_DATABASE_URL": "postgresql://platform/postgres",
        "SECRETS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(var, value)


def test_the_cli_needs_its_env(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    for var in ("ZM_API_DB_PASSWORD", "PLATFORM_DATABASE_URL", "SECRETS_ENCRYPTION_KEY"):
        monkeypatch.delenv(var, raising=False)
    args = ["provision-local", "pilot-local", "--dsn", "postgresql://x", "--operaton-url", "u"]
    with pytest.raises(SystemExit):
        worker.main(args)
    err = capsys.readouterr().err
    assert "ZM_API_DB_PASSWORD" in err and "SECRETS_ENCRYPTION_KEY" in err


def test_an_unknown_slug_needs_a_template(full_env: None, capsys) -> None:
    args = ["provision-local", "acme-local", "--dsn", "postgresql://x", "--operaton-url", "u"]
    with pytest.raises(SystemExit):
        worker.main(args)
    assert "--template" in capsys.readouterr().err


def test_the_cli_builds_the_client_from_args_and_env(
    full_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, local.LocalClient]] = []
    monkeypatch.setattr(local, "provision", lambda dsn, client: seen.append((dsn, client)) or [])
    worker.main(
        ["provision-local", "qsr-local", "--dsn", "postgresql://v", "--operaton-url", "http://e"]
    )
    dsn, client = seen[0]
    assert dsn == "postgresql://platform/postgres"
    assert (client.template, client.name, client.workspace_code) == (
        "qsr",
        "Qsr Local",
        "QSR-LOCAL",
    )
    assert client.passwords == {"zm_engine": "engine", "zm_api": "api"}
    assert client.service_password == "service"


class FakeConn:
    def __init__(self, calls: list[Any], template: Any) -> None:
        self.calls, self.template = calls, template

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> "FakeConn":
        self.calls.append(("sql", query.split()[0], params))
        return self

    def fetchone(self) -> Any:
        return self.template


def test_provision_migrates_registers_then_seeds(
    full_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(local.psycopg, "connect", lambda *a, **k: FakeConn(calls, ({"v": 1},)))
    monkeypatch.setattr(
        local.vault_migrate, "apply", lambda dsn, pw: calls.append(("migrate", dsn, pw)) or ["0004"]
    )
    monkeypatch.setattr(local, "seed", lambda dsn, wf, key: calls.append(("seed", dsn, wf, key)))
    client = local.LocalClient(
        slug="qsr-local",
        dsn="postgresql://postgres:postgres@localhost:5434/postgres",
        operaton_url="http://localhost:8081/engine-rest",
        template="qsr",
        name="QSR Local",
        workspace_code="QSR-LOCAL",
        passwords={"zm_api": "api", "zm_engine": "engine"},
        service_password="service",
    )
    assert local.provision("postgresql://platform/postgres", client) == ["0004"]
    kinds = [c[0] if c[0] != "sql" else c[1] for c in calls]
    assert kinds == ["select", "migrate", "insert", "seed"]  # template read before any change
    insert = calls[2][2]
    assert insert[:6] == (
        "qsr-local",
        "QSR Local",
        "QSR-LOCAL",
        "qsr",
        "http://localhost:8081/engine-rest",
        "files",
    )
    assert secrets.unseal(insert[6]) == {
        "zm_api_dsn": "postgresql://zm_api:api@localhost:5434/postgres",
        "service_account_password": "service",
    }
    assert calls[3] == ("seed", "postgresql://zm_api:api@localhost:5434/postgres", {"v": 1}, "qsr")


def test_provision_stops_before_changing_anything_without_the_template(
    full_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(local.psycopg, "connect", lambda *a, **k: FakeConn(calls, None))
    monkeypatch.setattr(local.vault_migrate, "apply", lambda *a: calls.append("migrate"))
    client = local.LocalClient("x", "postgresql://v", "u", "nope", "X", "X", {}, "s")
    with pytest.raises(LookupError, match="templates sync"):
        local.provision("postgresql://platform/postgres", client)
    assert "migrate" not in calls
