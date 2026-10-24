"""Provisioning queue (S2-02a, #24) on local pg-platform: `uv run pytest -m integration`.

Steps here are fakes with test-only names, so a running compose worker never claims these rows.
"""

import argparse
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import psycopg
import pytest
import respx
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from zm_api import secrets as sealed
from zm_worker import vault_migrate
from zm_worker.provision import STEPS, railway, runner, supabase
from zm_worker.provision.runner import Context, NeedsHuman, Settings

pytestmark = pytest.mark.integration

PORT = os.environ.get("PG_PLATFORM_PORT", 5433)
URL = f"postgresql://postgres:postgres@localhost:{PORT}/postgres"
INI = Path(__file__).resolve().parents[2] / "api/alembic.ini"
SETTINGS = Settings("t", "org", "ap-southeast-1", "micro", "zm-test-")


@pytest.fixture(autouse=True)
def platform_db(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", URL.replace("postgresql", "postgresql+asyncpg"))
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(URL, autocommit=True, row_factory=dict_row) as c:
        yield c


def new_client(conn: psycopg.Connection, *steps: str) -> uuid.UUID:
    slug = f"t-{uuid.uuid4().hex[:8]}"
    client_id = conn.execute(
        "insert into platform.clients (slug, name, workspace_code, template_key)"
        " values (%s, 'Test', %s, 'qsr') returning id",
        (slug, slug.upper()),
    ).fetchone()["id"]
    for seq, step in enumerate(steps, start=1):
        conn.execute(
            "insert into platform.provisioning_steps (client_id, step, seq) values (%s, %s, %s)",
            (client_id, f"{step}_{client_id.hex[:8]}", seq),
        )
    return client_id


def names(client_id: uuid.UUID, *steps: str) -> list[str]:
    return [f"{step}_{client_id.hex[:8]}" for step in steps]


def state(conn: psycopg.Connection, client_id: uuid.UUID) -> dict[str, dict]:
    rows = conn.execute(
        "select step, status::text, attempts, last_error from platform.provisioning_steps"
        " where client_id = %s order by seq",
        (client_id,),
    ).fetchall()
    return {row["step"].rsplit("_", 1)[0]: row for row in rows}


def client(conn: psycopg.Connection, client_id: uuid.UUID) -> dict:
    return conn.execute("select * from platform.clients where id = %s", (client_id,)).fetchone()


def rewind(conn: psycopg.Connection, client_id: uuid.UUID, hours: int = 1) -> None:
    """Pretend the backoff, lease or recheck interval has passed."""
    conn.execute(
        "update platform.provisioning_steps"
        " set updated_at = updated_at - make_interval(hours => %s) where client_id = %s",
        (hours, client_id),
    )


def run(conn: psycopg.Connection, steps: dict) -> int:
    with httpx.Client(base_url=supabase.API) as mgmt, httpx.Client() as http:
        return runner.run_pending(conn, steps, SETTINGS, mgmt, http)


def test_runs_steps_in_seq_order_and_persists_what_they_seal(conn) -> None:
    order: list[str] = []
    client_id = new_client(conn, "one", "two")
    one, two = names(client_id, "one", "two")

    def first(ctx: Context) -> None:
        order.append("one")
        ctx.seal(supabase_ref="ref1")

    def second(ctx: Context) -> None:
        order.append("two")
        assert ctx.secrets["supabase_ref"] == "ref1"
        ctx.register(supabase_ref="ref1", bucket="files")

    run(conn, {two: second, one: first})

    assert order == ["one", "two"]
    assert {s: r["status"] for s, r in state(conn, client_id).items()} == {
        "one": "done",
        "two": "done",
    }
    row = client(conn, client_id)
    assert row["status"] == "provisioning"  # ready only after S2-02b's last step
    assert (row["supabase_ref"], row["bucket"]) == ("ref1", "files")
    assert sealed.unseal(row["secrets"]) == {"supabase_ref": "ref1"}


def test_a_step_waits_for_the_one_before_it(conn) -> None:
    client_id = new_client(conn, "one", "two")
    one, two = names(client_id, "one", "two")
    ran: list[str] = []
    run(conn, {one: lambda ctx: ran.append("one"), two: lambda ctx: ran.append("two")})
    assert ran == ["one", "two"]

    # unknown steps (S2-02b's, until that worker code exists) are never claimed and block later ones
    other = new_client(conn, "later", "two")
    _, two_other = names(other, "later", "two")
    assert run(conn, {two_other: lambda ctx: ran.append("blocked")}) == 0
    assert state(conn, other)["later"]["status"] == "pending"


def test_killed_mid_step_resumes_after_the_lease(conn) -> None:
    client_id = new_client(conn, "one")
    (one,) = names(client_id, "one")
    calls: list[int] = []

    claimed = runner.claim(conn, [one])  # the worker took it, then was killed
    assert claimed is not None and state(conn, client_id)["one"]["status"] == "running"

    assert run(conn, {one: lambda ctx: calls.append(1)}) == 0  # lease still held
    conn.execute(
        "update platform.provisioning_steps set updated_at = now() - %s where client_id = %s",
        (runner.LEASE + runner.LEASE / 10, client_id),
    )
    assert run(conn, {one: lambda ctx: calls.append(1)}) == 1
    assert calls == [1]
    assert state(conn, client_id)["one"]["status"] == "done"


def test_heartbeat_keeps_the_lease(conn) -> None:
    client_id = new_client(conn, "one")
    (one,) = names(client_id, "one")
    seen: list[object] = []

    def slow(ctx: Context) -> None:
        conn.execute(
            "update platform.provisioning_steps set updated_at = now() - interval '1 hour'"
            " where client_id = %s",
            (client_id,),
        )
        ctx.heartbeat()
        seen.append(runner.claim(conn, [one]))  # a second worker must not take it

    run(conn, {one: slow})
    assert seen == [None]


def test_a_step_failing_5_times_is_failed_with_last_error(conn) -> None:
    client_id = new_client(conn, "one", "two")
    one, two = names(client_id, "one", "two")

    def boom(ctx: Context) -> None:
        raise RuntimeError("Management API said no")

    steps = {one: boom, two: lambda ctx: None}
    for attempt in range(1, runner.MAX_ATTEMPTS + 1):
        assert run(conn, steps) == 1
        assert run(conn, steps) == 0  # backing off
        assert state(conn, client_id)["one"]["attempts"] == attempt
        rewind(conn, client_id)

    steps_now = state(conn, client_id)
    assert steps_now["one"]["status"] == "failed"
    assert steps_now["one"]["last_error"] == "Management API said no"
    assert steps_now["two"]["status"] == "pending"
    assert client(conn, client_id)["status"] == "failed"
    assert run(conn, steps) == 0  # failed waits for a person (S2-01 retry)


def test_needs_human_is_rechecked_and_clears(conn) -> None:
    client_id = new_client(conn, "one")
    (one,) = names(client_id, "one")
    answers = [NeedsHuman("Dashboard → x → off"), None]

    def probe(ctx: Context) -> None:
        answer = answers.pop(0)
        if answer:
            raise answer

    run(conn, {one: probe})
    row = state(conn, client_id)["one"]
    assert (row["status"], row["last_error"], row["attempts"]) == (
        "needs_human",
        "Dashboard → x → off",
        0,
    )
    assert run(conn, {one: probe}) == 0  # not before the recheck interval
    rewind(conn, client_id)
    run(conn, {one: probe})
    assert state(conn, client_id)["one"]["status"] == "done"
    assert state(conn, client_id)["one"]["last_error"] is None


def test_two_workers_never_claim_the_same_step(conn) -> None:
    client_id = new_client(conn, "one")
    (one,) = names(client_id, "one")
    with psycopg.connect(URL, row_factory=dict_row) as other:
        # the other worker is mid-claim: it holds the row lock inside its transaction
        other.execute(
            "select 1 from platform.provisioning_steps where step = %s for update", (one,)
        )
        assert runner.claim(conn, [one]) is None
        other.rollback()
    assert runner.claim(conn, [one]) is not None


@respx.mock
def test_full_supabase_chain_survives_a_kill_after_the_project_post(conn, monkeypatch) -> None:
    """The real steps through the queue, Management API mocked: killed right after the POST,
    the restarted worker finds the project instead of creating a second one."""
    # real step names: rows left by an earlier run of this test would be claimed first
    conn.execute("delete from platform.provisioning_steps where step = any(%s)", (list(STEPS),))
    slug = f"t-{uuid.uuid4().hex[:8]}"
    client_id = conn.execute(
        "insert into platform.clients (slug, name, workspace_code, template_key)"
        " values (%s, 'Test', %s, 'qsr') returning id",
        (slug, slug.upper()),
    ).fetchone()["id"]
    for seq, step in enumerate([*supabase.STEPS, "operaton_service"], start=1):
        conn.execute(
            "insert into platform.provisioning_steps (client_id, step, seq) values (%s, %s, %s)",
            (client_id, step, seq),
        )
    name, ref, api = f"zm-test-{slug}", "r" * 20, supabase.API
    project = f"https://{ref}.supabase.co"
    org: list[dict] = []

    def create(request: httpx.Request) -> httpx.Response:
        org.append({"ref": ref, "name": name, "status": "COMING_UP"})
        raise KeyboardInterrupt  # the worker dies before it hears the answer

    respx.get(f"{api}/v1/organizations/org/projects").mock(
        side_effect=lambda request: httpx.Response(200, json={"projects": org})
    )
    respx.post(f"{api}/v1/projects").mock(side_effect=create)
    respx.get(f"{api}/v1/projects/{ref}").mock(
        return_value=httpx.Response(200, json={"ref": ref, "status": "ACTIVE_HEALTHY"})
    )
    respx.get(f"{api}/v1/projects/{ref}/config/database/pooler").mock(
        return_value=httpx.Response(200, json=[{"database_type": "PRIMARY", "db_host": "pooler"}])
    )
    respx.get(f"{api}/v1/projects/{ref}/api-keys").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "anon", "type": "legacy", "api_key": "anon"},
                {"name": "service_role", "type": "legacy", "api_key": "service"},
            ],
        )
    )
    respx.route(url=f"{api}/v1/projects/{ref}/postgrest").mock(
        return_value=httpx.Response(200, json={"db_schema": supabase.NO_EXPOSED_SCHEMAS})
    )
    respx.get(url__startswith=f"{project}/rest/v1/").mock(return_value=httpx.Response(404))
    respx.post(f"{project}/storage/v1/bucket").mock(return_value=httpx.Response(200))
    migrated: list[str] = []
    monkeypatch.setattr(vault_migrate, "apply", lambda dsn, pw: migrated.append(dsn) or [])

    with pytest.raises(KeyboardInterrupt):
        run(conn, supabase.STEPS)
    assert state(conn, client_id)["supabase"]["status"] == "running"  # step "supabase_project"

    rewind(conn, client_id)  # the lease ran out
    assert run(conn, supabase.STEPS) == 5

    assert len(org) == 1  # one POST in all: the one the kill cut off
    steps_now = {s: r["status"] for s, r in state(conn, client_id).items()}
    assert steps_now == {
        "supabase": "done",
        "vault": "done",
        "data_api": "done",
        "bucket": "done",
        "registry": "done",
        "operaton": "pending",  # S2-02b
    }
    row = client(conn, client_id)
    assert (row["status"], row["supabase_ref"], row["bucket"]) == ("provisioning", ref, "files")
    secrets = sealed.unseal(row["secrets"])
    assert secrets["supabase_ref"] == ref
    assert secrets["service_role_key"] == "service"
    assert secrets["zm_api_dsn"].startswith(f"postgresql://zm_api.{ref}:")
    assert {"db_password", "zm_api_password", "zm_engine_password"} <= secrets.keys()
    assert migrated == [
        f"postgresql://postgres.{ref}:{secrets['db_password']}@pooler:5432/postgres"
    ]


def test_seed_writes_the_first_draft_and_keeps_an_edited_one() -> None:
    """railway.seed on the migrated pg-iso vault, as the zm_api role the API uses. pg-iso is the
    qsr-local isolation client's vault, so its draft is put back afterwards."""
    iso = os.environ.get("PG_ISO_PORT", 5434)
    password = os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local")
    admin = f"postgresql://postgres:postgres@localhost:{iso}/postgres"
    dsn = f"postgresql+asyncpg://zm_api:{password}@localhost:{iso}/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        before = c.execute("select workflow, template_key, updated_by from app.drafts").fetchall()
        try:
            c.execute("delete from app.drafts")
            railway.seed(dsn, {"v": 1}, "qsr")
            railway.seed(dsn, {"v": 2}, "matrix-retail")  # a re-run must not overwrite edits
            rows = c.execute("select workflow, template_key from app.drafts").fetchall()
            assert rows == [({"v": 1}, "qsr")]
        finally:
            c.execute("delete from app.drafts")
            for workflow, key, by in before:
                c.execute(
                    "insert into app.drafts (id, workflow, template_key, updated_by)"
                    " values (1, %s, %s, %s)",
                    (Jsonb(workflow), key, by),
                )
