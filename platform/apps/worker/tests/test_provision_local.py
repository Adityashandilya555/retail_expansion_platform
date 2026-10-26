"""`zm-worker provision-local` (#31) without the stack: the CLI's checks, the DSN it registers,
and the order of its steps. The isolation suite runs it for real (`pytest -m isolation`)."""

from typing import Any

import httpx
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
        "ZM_ISO_API_DB_PASSWORD": "iso-api",
        "ZM_ISO_ENGINE_DB_PASSWORD": "iso-engine",
        "ZM_ISO_ADMIN_USER_PASSWORD": "iso-service",
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


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, local.LocalClient]]:
    calls: list[tuple[str, local.LocalClient]] = []
    monkeypatch.setattr(local, "provision", lambda dsn, client: calls.append((dsn, client)) or [])
    return calls


def test_the_isolation_client_gets_its_own_credentials(
    full_env: None, seen: list[tuple[str, local.LocalClient]]
) -> None:
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
    # never pilot's: applying those would reset qsr's vault roles and seal the wrong password
    assert client.passwords == {"zm_engine": "iso-engine", "zm_api": "iso-api"}
    assert client.service_password == "iso-service"


def test_the_pilot_client_gets_the_default_credentials(
    full_env: None, seen: list[tuple[str, local.LocalClient]]
) -> None:
    worker.main(
        ["provision-local", "pilot-local", "--dsn", "postgresql://v", "--operaton-url", "http://e"]
    )
    client = seen[0][1]
    assert client.template == "matrix-retail"
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
    assert local.provision("postgresql://platform/postgres", client, engine(200, 401)) == ["0004"]
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


def engine(signed_in: int, anonymous: int) -> httpx.Client:
    """An engine answering the sign-in probe: the service account, then anonymous."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/engine-rest/task")
        return httpx.Response(signed_in if "authorization" in request.headers else anonymous)

    return httpx.Client(transport=httpx.MockTransport(handler))


CLIENT = local.LocalClient(
    "qsr-local", "postgresql://v", "http://localhost:8081/engine-rest", "qsr", "Q", "Q", {}, "pw"
)


def test_the_engine_probe_needs_the_account_and_refuses_anonymous() -> None:
    local.check_engine(CLIENT, engine(200, 401))
    with pytest.raises(RuntimeError, match="can't query tasks: 401"):
        local.check_engine(CLIENT, engine(401, 401))  # a wrong service password
    with pytest.raises(RuntimeError, match="anonymous task query answered 200"):
        local.check_engine(CLIENT, engine(200, 200))  # auth switched off


def test_a_failing_probe_registers_nothing(full_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(local.psycopg, "connect", lambda *a, **k: FakeConn(calls, ({"v": 1},)))
    monkeypatch.setattr(local.vault_migrate, "apply", lambda *a: calls.append("migrate"))
    with pytest.raises(RuntimeError):
        local.provision("postgresql://platform/postgres", CLIENT, engine(401, 401))
    assert "migrate" not in calls and not any(c[1] == "insert" for c in calls if c != "migrate")
