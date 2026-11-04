"""Client vault migrations (S1-03b, #12).

Integration tests run against both local vaults from scripts/dev-up.sh (Supabase CLI :54322 and
pg-iso), with Operaton already started as zm_engine: `uv run pytest -m integration`.
"""

import os
import shutil
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet

import zm_worker.__main__ as worker_main
from zm_api import secrets as sealed
from zm_worker import vault_migrate

SECRETS = {
    "zm_engine": os.environ.get("ZM_ENGINE_DB_PASSWORD", "zm-engine-local"),
    "zm_api": os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local"),
}
PG_ISO = int(os.environ.get("PG_ISO_PORT", 5434))
# qsr-local's vault has its own role passwords (#31); applying pilot's there would reset them
ISO_SECRETS = {
    "zm_engine": os.environ.get("ZM_ISO_ENGINE_DB_PASSWORD", "zm-engine-iso-local"),
    "zm_api": os.environ.get("ZM_ISO_API_DB_PASSWORD", "zm-api-iso-local"),
}
VAULT_PORTS = [54322, PG_ISO]


def secrets_for(port: int) -> dict[str, str]:
    return ISO_SECRETS if port == PG_ISO else SECRETS


def url(port: int, user: str = "postgres", password: str = "postgres") -> str:
    return f"postgresql://{user}:{password}@localhost:{port}/postgres"


def connect(port: int, role: str | None = None) -> psycopg.Connection:
    if role is None:
        return psycopg.connect(url(port), autocommit=True, connect_timeout=5)
    password = secrets_for(port)[role]
    return psycopg.connect(url(port, role, password), autocommit=True, connect_timeout=5)


def test_cli_passes_passwords_from_env(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        vault_migrate, "apply", lambda dsn, secrets: calls.append((dsn, secrets)) or []
    )
    monkeypatch.setenv("ZM_ENGINE_DB_PASSWORD", "e")
    monkeypatch.setenv("ZM_API_DB_PASSWORD", "a")
    worker_main.main(["vault-migrate", "--dsn", "postgresql://x"])
    assert calls == [("postgresql://x", {"zm_engine": "e", "zm_api": "a"})]


def test_cli_refuses_without_passwords(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ZM_ENGINE_DB_PASSWORD", raising=False)
    monkeypatch.delenv("ZM_API_DB_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        worker_main.main(["vault-migrate", "--dsn", "postgresql://x"])
    assert "ZM_ENGINE_DB_PASSWORD" in capsys.readouterr().err


# --- `vault-migrate --ready`: the API's pre-deploy over every ready client ----------------------


@pytest.fixture
def key(monkeypatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def cloud(ref: str) -> bytes:
    """A ready client's secrets as cloud provisioning (S2-02a) seals them."""
    return sealed.seal(
        {
            "supabase_ref": ref,
            "db_password": f"db-{ref}",
            "pooler_host": "aws-0-eu.pooler.example",
            "zm_api_password": f"api-{ref}",
            "zm_engine_password": f"engine-{ref}",
            "zm_api_dsn": "unused here",
        }
    )


def test_ready_cloud_vaults_are_migrated_and_local_ones_skipped(key, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        vault_migrate, "apply", lambda dsn, pw: calls.append((dsn, pw)) or ["0007_x.sql"]
    )
    local = sealed.seal({"zm_api_dsn": "postgresql://zm_api:x@localhost:54322/postgres"})
    done = vault_migrate.apply_ready(
        [("matrix", cloud("abc")), ("pilot-local", local), ("x", None)]
    )
    assert done == {"matrix": ["0007_x.sql"]}
    assert calls == [  # as the superuser, through the session pooler, like provisioning
        (
            "postgresql://postgres.abc:db-abc@aws-0-eu.pooler.example:5432/postgres",
            {"zm_engine": "engine-abc", "zm_api": "api-abc"},
        )
    ]


def test_one_client_failing_doesnt_stop_the_others_but_fails_the_run(key, monkeypatch) -> None:
    tried = []

    def apply(dsn: str, _pw: dict[str, str]) -> list[str]:
        tried.append(dsn.split(":")[1])
        if "postgres.down" in dsn:
            raise psycopg.OperationalError("vault down")
        return []

    monkeypatch.setattr(vault_migrate, "apply", apply)
    clients = [("down", cloud("down")), ("other-key", b"not a fernet token"), ("up", cloud("up"))]
    with pytest.raises(RuntimeError, match="failed for down, other-key"):
        vault_migrate.apply_ready(clients)
    assert tried == ["//postgres.down", "//postgres.up"]


def test_cli_ready_needs_the_registry_and_takes_no_dsn(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PLATFORM_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        worker_main.main(["vault-migrate", "--ready"])
    assert "PLATFORM_DATABASE_URL" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        worker_main.main(["vault-migrate", "--ready", "--dsn", "postgresql://x"])
    with pytest.raises(SystemExit):
        worker_main.main(["vault-migrate"])


def test_cli_ready_migrates_what_the_registry_lists(key, monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql+asyncpg://registry")
    listed = [("matrix", cloud("abc"))]
    monkeypatch.setattr(vault_migrate, "ready_clients", lambda dsn: listed)
    seen = []
    monkeypatch.setattr(vault_migrate, "apply_ready", lambda clients: seen.append(clients) or {})
    worker_main.main(["vault-migrate", "--ready"])
    assert seen == [listed]


def test_apply_refuses_missing_password() -> None:
    with pytest.raises(ValueError, match="zm_api"):
        vault_migrate.apply("postgresql://unused", {"zm_engine": "e"})


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_second_run_applies_nothing(port: int) -> None:
    vault_migrate.apply(url(port), secrets_for(port))
    assert vault_migrate.apply(url(port), secrets_for(port)) == []


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
@pytest.mark.parametrize("table", ["app.schema_migrations", "operaton.act_ge_property"])
def test_anon_cannot_read(port: int, table: str) -> None:
    with connect(port) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("set role anon")
        conn.execute(f"select * from {table}")


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_engine_cannot_read_app(port: int) -> None:
    with connect(port, "zm_engine") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from app.schema_migrations")


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_api_cannot_touch_migration_history(port: int) -> None:
    with connect(port, "zm_api") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from app.schema_migrations")


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_api_cannot_write_operaton(port: int) -> None:
    with connect(port, "zm_api") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("insert into operaton.act_ru_task (id_) values ('x')")


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_operaton_tables_are_created_by_zm_engine_in_operaton(port: int) -> None:
    with connect(port) as conn:
        owners = conn.execute(
            "select schemaname, tableowner, count(*) from pg_tables "
            "where tablename like 'act\\_%' group by 1, 2"
        ).fetchall()
    assert len(owners) == 1, owners
    assert owners[0][:2] == ("operaton", "zm_engine")


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_edited_migration_fails_clearly(port: int, tmp_path: Path) -> None:
    vault_migrate.apply(url(port), secrets_for(port))
    first = sorted(vault_migrate.MIGRATIONS_DIR.glob("*.sql"))[0]
    edited = tmp_path / first.name
    shutil.copy(first, edited)
    edited.write_text(edited.read_text() + "\n-- edited after it was applied\n")
    with pytest.raises(vault_migrate.MigrationChangedError, match=f"{first.name} was already"):
        vault_migrate.apply(url(port), secrets_for(port), tmp_path)
