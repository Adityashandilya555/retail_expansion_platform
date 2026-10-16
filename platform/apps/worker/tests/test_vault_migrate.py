"""Client vault migrations (S1-03b, #12).

Integration tests run against both local vaults from scripts/dev-up.sh (Supabase CLI :54322 and
pg-iso), with Operaton already started as zm_engine: `uv run pytest -m integration`.
"""

import os
import shutil
from pathlib import Path

import psycopg
import pytest

import zm_worker.__main__ as worker_main
from zm_worker import vault_migrate

SECRETS = {
    "zm_engine": os.environ.get("ZM_ENGINE_DB_PASSWORD", "zm-engine-local"),
    "zm_api": os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local"),
}
VAULT_PORTS = [54322, int(os.environ.get("PG_ISO_PORT", 5434))]


def url(port: int, user: str = "postgres", password: str = "postgres") -> str:
    return f"postgresql://{user}:{password}@localhost:{port}/postgres"


def connect(port: int, role: str | None = None) -> psycopg.Connection:
    if role is None:
        return psycopg.connect(url(port), autocommit=True, connect_timeout=5)
    return psycopg.connect(url(port, role, SECRETS[role]), autocommit=True, connect_timeout=5)


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


def test_apply_refuses_missing_password() -> None:
    with pytest.raises(ValueError, match="zm_api"):
        vault_migrate.apply("postgresql://unused", {"zm_engine": "e"})


@pytest.mark.integration
@pytest.mark.parametrize("port", VAULT_PORTS)
def test_second_run_applies_nothing(port: int) -> None:
    vault_migrate.apply(url(port), SECRETS)
    assert vault_migrate.apply(url(port), SECRETS) == []


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
    vault_migrate.apply(url(port), SECRETS)
    first = sorted(vault_migrate.MIGRATIONS_DIR.glob("*.sql"))[0]
    edited = tmp_path / first.name
    shutil.copy(first, edited)
    edited.write_text(edited.read_text() + "\n-- edited after it was applied\n")
    with pytest.raises(vault_migrate.MigrationChangedError, match=f"{first.name} was already"):
        vault_migrate.apply(url(port), SECRETS, tmp_path)
