"""Provisioning steps for the client's Supabase project (S2-02a, #24), Management API mocked.

The queue (SKIP LOCKED, backoff, resume after a kill) needs Postgres: test_provision_queue.py.
"""

import json
import string
import threading
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

import zm_worker.__main__ as worker_main
from zm_worker import dsn, provision, vault_migrate
from zm_worker.provision import runner, supabase
from zm_worker.provision.runner import Context, NeedsHuman, Settings

API = "https://api.supabase.com"
REF = "abcdefghijklmnopqrst"
PROJECT = f"https://{REF}.supabase.co"
SETTINGS = Settings(
    access_token="sbp_test",
    org="zm-org",
    region="ap-southeast-1",
    instance_size="micro",
    prefix="zm-",
)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


class Recorder:
    """What a step persisted, standing in for platform.clients."""

    def __init__(self, secrets: dict[str, Any] | None = None) -> None:
        self.secrets: dict[str, Any] = dict(secrets or {})
        self.columns: dict[str, Any] = {}
        self.heartbeats = 0


def context(rec: Recorder, now: list[float] | None = None) -> Context:
    clock = now if now is not None else [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    return Context(
        client_id="00000000-0000-0000-0000-000000000001",
        slug="pilot",
        settings=SETTINGS,
        secrets=rec.secrets,
        mgmt=httpx.Client(base_url=API, headers={"Authorization": "Bearer sbp_test"}),
        http=httpx.Client(),
        persist_secrets=lambda data: rec.secrets.update(data),
        persist_columns=lambda cols: rec.columns.update(cols),
        heartbeat=lambda: setattr(rec, "heartbeats", rec.heartbeats + 1),
        sleep=sleep,
        clock=lambda: clock[0],
    )


def projects(*items: dict[str, str]) -> httpx.Response:
    return httpx.Response(200, json={"projects": list(items)})


# --- step 1: supabase_project -----------------------------------------------------------------


@respx.mock
def test_creates_the_project_when_missing_and_waits_until_healthy() -> None:
    rec = Recorder()
    respx.get(f"{API}/v1/organizations/zm-org/projects", params={"search": "zm-pilot"}).mock(
        return_value=projects({"ref": "other", "name": "zm-pilot-old", "status": "ACTIVE_HEALTHY"})
    )

    def create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # sealed before the POST, so a kill right after it can't lose the password, and the
        # name marks the project as this client's
        assert rec.secrets["db_password"] == body["db_pass"]
        assert rec.secrets["project_name"] == "zm-pilot"
        return httpx.Response(201, json={"ref": REF, "name": "zm-pilot", "status": "COMING_UP"})

    post = respx.post(f"{API}/v1/projects").mock(side_effect=create)
    respx.get(f"{API}/v1/projects/{REF}").mock(
        side_effect=[
            httpx.Response(200, json={"ref": REF, "status": "COMING_UP"}),
            httpx.Response(200, json={"ref": REF, "status": "ACTIVE_HEALTHY"}),
        ]
    )

    supabase.supabase_project(context(rec))

    body = json.loads(post.calls.last.request.content)
    assert body == {
        "name": "zm-pilot",
        "organization_slug": "zm-org",
        "region": "ap-southeast-1",
        "desired_instance_size": "micro",
        "db_pass": body["db_pass"],
    }
    assert len(body["db_pass"]) == 32
    assert set(body["db_pass"]) <= set(string.ascii_letters + string.digits)
    assert rec.secrets["supabase_ref"] == REF
    assert rec.heartbeats >= 1


@respx.mock
def test_resume_finds_the_existing_project_and_creates_none() -> None:
    """Killed after the POST, before the ref was saved: the re-run must not create a second one."""
    rec = Recorder({"db_password": "p" * 32, "project_name": "zm-pilot"})
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects(
            {"ref": "gone", "name": "zm-pilot", "status": "REMOVED"},
            {"ref": REF, "name": "zm-pilot", "status": "COMING_UP"},
        )
    )
    post = respx.post(f"{API}/v1/projects")
    respx.get(f"{API}/v1/projects/{REF}").mock(
        return_value=httpx.Response(200, json={"ref": REF, "status": "ACTIVE_HEALTHY"})
    )

    supabase.supabase_project(context(rec))

    assert not post.called
    assert rec.secrets == {"db_password": "p" * 32, "project_name": "zm-pilot", "supabase_ref": REF}


@pytest.mark.parametrize("secrets", [{}, {"db_password": "p" * 32}])
@respx.mock
def test_a_same_named_project_this_client_did_not_create_needs_a_human(secrets: dict) -> None:
    """E.g. a test worker and production's `zm-<slug>`: never adopted, never touched."""
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects({"ref": REF, "name": "zm-pilot", "status": "ACTIVE_HEALTHY"})
    )
    with pytest.raises(NeedsHuman, match=f"zm-pilot \\({REF}\\) exists that"):
        supabase.supabase_project(context(Recorder(secrets)))
    # only the lookup: no password reset, no POST
    assert [c.request.method for c in respx.calls] == ["GET"]


@respx.mock
def test_two_live_projects_with_the_name_is_an_error() -> None:
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects(
            {"ref": "a", "name": "zm-pilot", "status": "ACTIVE_HEALTHY"},
            {"ref": "b", "name": "zm-pilot", "status": "ACTIVE_HEALTHY"},
        )
    )
    with pytest.raises(RuntimeError, match="2 projects named zm-pilot"):
        supabase.supabase_project(context(Recorder()))


@respx.mock
def test_project_that_never_gets_healthy_times_out_after_10_minutes() -> None:
    now = [0.0]
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects({"ref": REF, "name": "zm-pilot", "status": "COMING_UP"})
    )
    respx.get(f"{API}/v1/projects/{REF}").mock(
        return_value=httpx.Response(200, json={"ref": REF, "status": "COMING_UP"})
    )
    with pytest.raises(TimeoutError, match="COMING_UP"):
        supabase.supabase_project(context(Recorder({"supabase_ref": REF}), now))
    assert 600 <= now[0] < 700


@respx.mock
def test_project_that_fails_to_start_raises_at_once() -> None:
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects({"ref": REF, "name": "zm-pilot", "status": "INIT_FAILED"})
    )
    respx.get(f"{API}/v1/projects/{REF}").mock(
        return_value=httpx.Response(200, json={"ref": REF, "status": "INIT_FAILED"})
    )
    with pytest.raises(RuntimeError, match="INIT_FAILED"):
        supabase.supabase_project(context(Recorder({"supabase_ref": REF})))


# --- step 2: vault_migrations -----------------------------------------------------------------


@respx.mock
def test_vault_migrations_run_through_the_session_pooler(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(vault_migrate, "apply", lambda dsn, pw: calls.append((dsn, pw)) or [])
    respx.get(f"{API}/v1/projects/{REF}/config/database/pooler").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"database_type": "READ_REPLICA", "db_host": "replica.example", "db_port": 6543},
                {
                    "database_type": "PRIMARY",
                    "db_host": "aws-1-ap-southeast-1.pooler.supabase.com",
                    "db_port": 6543,
                },
            ],
        )
    )
    rec = Recorder({"supabase_ref": REF, "db_password": "dbpw"})

    supabase.vault_migrations(context(rec))

    host = "aws-1-ap-southeast-1.pooler.supabase.com"
    ((dsn, passwords),) = calls
    assert dsn == f"postgresql://postgres.{REF}:dbpw@{host}:5432/postgres"
    assert passwords == {
        "zm_api": rec.secrets["zm_api_password"],
        "zm_engine": rec.secrets["zm_engine_password"],
    }
    assert rec.secrets["pooler_host"] == host
    assert rec.secrets["zm_api_dsn"] == (
        f"postgresql://zm_api.{REF}:{rec.secrets['zm_api_password']}@{host}:5432/postgres"
    )

    # a re-run keeps the passwords, so the sealed DSN stays valid
    before = dict(rec.secrets)
    supabase.vault_migrations(context(rec))
    assert rec.secrets == before
    assert calls[1] == calls[0]


# --- step 3: data_api_off ---------------------------------------------------------------------

KEYS = [
    {"name": "anon", "type": "legacy", "api_key": "anon-jwt"},
    {"name": "service_role", "type": "legacy", "api_key": "service-jwt"},
    {"name": "default", "type": "publishable", "api_key": "sb_publishable_x"},
]


def mock_keys_and_patch(exposed: str = supabase.NO_EXPOSED_SCHEMAS) -> respx.Route:
    respx.get(f"{API}/v1/projects/{REF}/api-keys", params={"reveal": "true"}).mock(
        return_value=httpx.Response(200, json=KEYS)
    )
    respx.get(f"{API}/v1/projects/{REF}/postgrest").mock(
        return_value=httpx.Response(200, json={"db_schema": exposed})
    )
    return respx.patch(f"{API}/v1/projects/{REF}/postgrest").mock(
        return_value=httpx.Response(200, json={"db_schema": exposed})
    )


@respx.mock
def test_data_api_off_when_the_anon_probe_fails() -> None:
    patch = mock_keys_and_patch()
    probes = respx.get(url__startswith=f"{PROJECT}/rest/v1/").mock(
        return_value=httpx.Response(404, json={"message": "schema not exposed"})
    )
    rec = Recorder({"supabase_ref": REF})

    supabase.data_api_off(context(rec))

    assert json.loads(patch.calls.last.request.content) == {
        "db_schema": supabase.NO_EXPOSED_SCHEMAS
    }
    assert probes.call_count == 3  # the root, and a table in schema app and in operaton
    assert {c.request.headers["apikey"] for c in probes.calls} == {"anon-jwt"}
    assert [c.request.headers.get("accept-profile") for c in probes.calls] == [
        None,
        "app",
        "operaton",
    ]
    assert rec.secrets["service_role_key"] == "service-jwt"


@respx.mock
def test_data_api_still_answering_needs_a_human() -> None:
    mock_keys_and_patch()
    probes = respx.get(url__startswith=f"{PROJECT}/rest/v1/").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(NeedsHuman) as err:
        supabase.data_api_off(context(Recorder({"supabase_ref": REF})))
    assert "Dashboard → zm-pilot → Integrations → Data API → off" in str(err.value)
    assert probes.call_count == 3 * supabase.PROBE_TRIES  # asked only after every try


@respx.mock
def test_data_api_probe_waits_for_postgrest_to_reload() -> None:
    mock_keys_and_patch()
    answers = iter([200, 200, 200] + [404] * 3)  # still the old config for the first round
    respx.get(url__startswith=f"{PROJECT}/rest/v1/").mock(
        side_effect=lambda request: httpx.Response(next(answers), json={})
    )
    now = [0.0]
    supabase.data_api_off(context(Recorder({"supabase_ref": REF}), now))
    assert now[0] == supabase.POLL_SECONDS


@respx.mock
def test_data_api_config_that_did_not_take_needs_a_human() -> None:
    mock_keys_and_patch(exposed="public, graphql_public")
    probes = respx.get(url__startswith=f"{PROJECT}/rest/v1/")
    with pytest.raises(NeedsHuman, match="still exposes 'public, graphql_public'"):
        supabase.data_api_off(context(Recorder({"supabase_ref": REF})))
    assert not probes.called


# --- step 4: bucket ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "created",
    [
        httpx.Response(200, json={"name": "files"}),
        httpx.Response(409, json={"error": "Duplicate"}),
        httpx.Response(400, json={"statusCode": "409", "error": "Duplicate"}),
    ],
)
@respx.mock
def test_bucket_is_private_whether_new_or_existing(created: httpx.Response) -> None:
    post = respx.post(f"{PROJECT}/storage/v1/bucket").mock(return_value=created)
    put = respx.put(f"{PROJECT}/storage/v1/bucket/files").mock(
        return_value=httpx.Response(200, json={"message": "Successfully updated"})
    )

    supabase.bucket(context(Recorder({"supabase_ref": REF, "service_role_key": "service-jwt"})))

    request = post.calls.last.request
    assert json.loads(request.content) == {"id": "files", "name": "files", "public": False}
    assert request.headers["apikey"] == "service-jwt"
    assert request.headers["authorization"] == "Bearer service-jwt"
    assert put.called == (created.status_code != 200)
    if put.called:
        assert json.loads(put.calls.last.request.content) == {"public": False}


@respx.mock
def test_new_style_secret_key_goes_only_in_the_apikey_header() -> None:
    post = respx.post(f"{PROJECT}/storage/v1/bucket").mock(return_value=httpx.Response(200))
    supabase.bucket(context(Recorder({"supabase_ref": REF, "service_role_key": "sb_secret_x"})))
    assert "authorization" not in post.calls.last.request.headers


@respx.mock
def test_bucket_error_raises() -> None:
    respx.post(f"{PROJECT}/storage/v1/bucket").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        supabase.bucket(context(Recorder({"supabase_ref": REF, "service_role_key": "k"})))


# --- step 5: registry -------------------------------------------------------------------------


def test_registry_writes_ref_and_bucket() -> None:
    rec = Recorder({"supabase_ref": REF})
    supabase.registry(context(rec))
    assert rec.columns == {"supabase_ref": REF, "bucket": "files"}


def test_steps_in_order() -> None:
    assert list(supabase.STEPS) == [
        "supabase_project",
        "vault_migrations",
        "data_api_off",
        "bucket",
        "registry",
    ]


# --- outcome of one attempt -------------------------------------------------------------------


def ok(_: Context) -> None:
    return None


def boom(_: Context) -> None:
    raise RuntimeError("Management API said no")


def stopped(_: Context) -> None:
    raise runner.Stopped


def human(_: Context) -> None:
    raise NeedsHuman("Dashboard → somewhere → off")


def test_outcomes() -> None:
    ctx = context(Recorder())
    assert runner.attempt(ok, ctx, attempts=0) == ("done", None, 0)
    assert runner.attempt(human, ctx, attempts=2) == (
        "needs_human",
        "Dashboard → somewhere → off",
        2,
    )
    assert runner.attempt(boom, ctx, attempts=0) == ("pending", "Management API said no", 1)
    assert runner.attempt(boom, ctx, attempts=runner.MAX_ATTEMPTS - 1) == (
        "failed",
        "Management API said no",
        runner.MAX_ATTEMPTS,
    )
    # shutting down hands the step back without using an attempt
    assert runner.attempt(stopped, ctx, attempts=2) == ("pending", None, 2)


def test_secrets_never_reach_last_error_or_the_log(caplog) -> None:
    def echoed(ctx: Context) -> None:  # e.g. an API repeating a rejected input value
        raise RuntimeError(f"invalid value {ctx.secrets['zm_engine_password']!r} for DB_PASSWORD")

    ctx = context(Recorder({"zm_engine_password": "s3cret-engine-pw", "supabase_ref": REF}))
    _, error, _ = runner.attempt(echoed, ctx, attempts=0)
    assert error == "invalid value '***' for DB_PASSWORD"
    assert "s3cret-engine-pw" not in caplog.text
    assert runner.redact(f"project {REF}", ctx.secrets) == f"project {REF}"  # not a secret


def test_http_errors_keep_the_response_body_but_no_token() -> None:
    def denied(_: Context) -> None:
        request = httpx.Request("POST", f"{API}/v1/projects", headers={"Authorization": "Bearer t"})
        response = httpx.Response(402, json={"message": "billing"}, request=request)
        response.raise_for_status()

    status, error, _ = runner.attempt(denied, context(Recorder()), attempts=0)
    assert status == "pending"
    assert error is not None and "402" in error and "billing" in error
    assert "Bearer" not in error


# --- wiring ----------------------------------------------------------------------------------

CONFIGURED = {
    "PLATFORM_DATABASE_URL": "postgresql://unused/postgres",
    "SECRETS_ENCRYPTION_KEY": "set by the key fixture",
    "SUPABASE_ACCESS_TOKEN": "t",
    "SUPABASE_ORG_ID": "o",
    "SUPABASE_PROJECT_PREFIX": "zm-test-",
}


def configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(provision, "loop", lambda *args: calls.append(args))
    for var, value in {**CONFIGURED, **env}.items():
        if var == "SECRETS_ENCRYPTION_KEY" and value:
            continue
        if value:
            monkeypatch.setenv(var, value)
        else:
            monkeypatch.delenv(var, raising=False)
    return calls


def test_provisioning_runs_in_its_own_thread(monkeypatch) -> None:
    calls = configure(monkeypatch)
    stop = threading.Event()
    thread = worker_main.start_provisioner(stop, 5.0)
    assert thread is not None
    thread.join(timeout=5)
    ((dsn, settings, passed_stop, interval),) = calls
    assert (dsn, settings.prefix, passed_stop, interval) == (
        CONFIGURED["PLATFORM_DATABASE_URL"],
        "zm-test-",
        stop,
        5.0,
    )


@pytest.mark.parametrize(
    "missing", ["PLATFORM_DATABASE_URL", "SUPABASE_ACCESS_TOKEN", "SUPABASE_PROJECT_PREFIX"]
)
def test_provisioning_off_until_configured(monkeypatch, missing: str) -> None:
    calls = configure(monkeypatch, **{missing: ""})
    assert worker_main.start_provisioner(threading.Event(), 5.0) is None
    assert calls == []


def test_a_failing_pass_is_logged_and_the_loop_goes_on(monkeypatch, caplog) -> None:
    stop = threading.Event()
    passes: list[int] = []

    def boom(*args: object) -> int:
        passes.append(1)
        if len(passes) == 2:
            stop.set()
        raise OSError("registry down")

    monkeypatch.setattr(provision, "run_pending", boom)
    monkeypatch.setattr(provision.psycopg, "connect", lambda *a, **k: FakeConn())
    provision.loop("postgresql://unused/postgres", SETTINGS, stop, 0)
    assert len(passes) == 2
    assert [r.getMessage() for r in caplog.records].count("provisioning pass failed") == 2


class FakeConn:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_one_connection_across_passes(monkeypatch) -> None:
    stop = threading.Event()
    conns: list[FakeConn] = []
    seen: list[FakeConn] = []

    def connect(*args: object, **kwargs: object) -> FakeConn:
        conns.append(FakeConn())
        return conns[-1]

    def run_pending(conn: FakeConn, *args: object) -> int:
        seen.append(conn)
        if len(seen) == 3:
            stop.set()
        return 0

    monkeypatch.setattr(provision.psycopg, "connect", connect)
    monkeypatch.setattr(provision, "run_pending", run_pending)
    provision.loop("postgresql+asyncpg://u:p@h/db", SETTINGS, stop, 0)
    assert len(conns) == 1 and seen == conns * 3
    assert conns[0].closed


def test_settings_from_env(monkeypatch) -> None:
    for var, value in {
        "SUPABASE_ACCESS_TOKEN": "t",
        "SUPABASE_ORG_ID": "o",
        "SUPABASE_REGION": "ap-southeast-1",
        "SUPABASE_PROJECT_PREFIX": "zm-test-",
    }.items():
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("SUPABASE_CLIENT_INSTANCE_SIZE", raising=False)
    assert Settings.from_env() == Settings("t", "o", "ap-southeast-1", "micro", "zm-test-")
    # no production default: without a prefix the worker provisions nothing
    monkeypatch.delenv("SUPABASE_PROJECT_PREFIX")
    assert Settings.from_env() is None


def test_libpq_dsn() -> None:
    assert dsn.libpq("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert dsn.libpq("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
