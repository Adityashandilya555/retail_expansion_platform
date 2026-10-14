"""Smoke test of the local stack started by scripts/dev-up.sh (S1-01 acceptance criteria).

Host ports follow docker-compose.yml, including its overrides (API_PORT, OPERATON_PILOT_PORT, ...).
"""

import os

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.integration


def port(var: str, default: int) -> int:
    return int(os.environ.get(var, default))


SUPABASE_DB = 54322
PG_PLATFORM = port("PG_PLATFORM_PORT", 5433)
PG_ISO = port("PG_ISO_PORT", 5434)


@pytest.mark.parametrize(
    "url",
    [
        f"http://localhost:{port('API_PORT', 8000)}/healthz",
        f"http://localhost:{port('OPERATON_PILOT_PORT', 8080)}/engine-rest/engine",
        f"http://localhost:{port('OPERATON_ISO_PORT', 8081)}/engine-rest/engine",
    ],
)
def test_http_endpoint_returns_200(url: str) -> None:
    assert httpx.get(url, timeout=10).status_code == 200


def connect(db_port: int) -> psycopg.Connection:
    return psycopg.connect(
        f"postgresql://postgres:postgres@localhost:{db_port}/postgres", connect_timeout=5
    )


@pytest.mark.parametrize("db_port", [SUPABASE_DB, PG_PLATFORM, PG_ISO])
def test_postgres_answers(db_port: int) -> None:
    with connect(db_port) as conn:
        assert conn.execute("select 1").fetchone() == (1,)


@pytest.mark.parametrize("db_port", [SUPABASE_DB, PG_ISO])
def test_operaton_tables_live_in_operaton_schema(db_port: int) -> None:
    with connect(db_port) as conn:
        row = conn.execute(
            "select count(*) from information_schema.tables "
            "where table_schema = 'operaton' and table_name = 'act_ge_property'"
        ).fetchone()
    assert row == (1,)
