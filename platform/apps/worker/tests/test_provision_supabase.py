"""Provisioning steps for the client's Supabase project (S2-02a, #24), Management API mocked.

The queue (SKIP LOCKED, backoff, resume after a kill) needs Postgres: test_provision_queue.py.
"""

import json
import string
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

import zm_worker.__main__ as worker_main
from zm_worker import provision, vault_migrate
from zm_worker.provision import STEPS, runner, supabase
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
        # sealed before the POST, so a kill right after it can't lose the password
        assert rec.secrets["db_password"] == body["db_pass"]
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
    rec = Recorder({"db_password": "p" * 32})
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects(
            {"ref": "gone", "name": "zm-pilot", "status": "REMOVED"},
            {"ref": REF, "name": "zm-pilot", "status": "COMING_UP"},
        )
    )
    post = respx.post(f"{API}/v1/projects")
    reset = respx.patch(f"{API}/v1/projects/{REF}/database/password")
    respx.get(f"{API}/v1/projects/{REF}").mock(
        return_value=httpx.Response(200, json={"ref": REF, "status": "ACTIVE_HEALTHY"})
    )

    supabase.supabase_project(context(rec))

    assert not post.called
    assert not reset.called
    assert rec.secrets == {"db_password": "p" * 32, "supabase_ref": REF}


@respx.mock
def test_existing_project_without_a_sealed_password_gets_a_new_one() -> None:
    rec = Recorder()
    respx.get(f"{API}/v1/organizations/zm-org/projects").mock(
        return_value=projects({"ref": REF, "name": "zm-pilot", "status": "ACTIVE_HEALTHY"})
    )
    reset = respx.patch(f"{API}/v1/projects/{REF}/database/password").mock(
        return_value=httpx.Response(200, json={"message": "ok"})
    )
    respx.get(f"{API}/v1/projects/{REF}").mock(
        return_value=httpx.Response(200, json={"ref": REF, "status": "ACTIVE_HEALTHY"})
    )

    supabase.supabase_project(context(rec))

    assert json.loads(reset.calls.last.request.content) == {"password": rec.secrets["db_password"]}


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
        supabase.supabase_project(context(Recorder({"db_password": "x"}), now))
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
        supabase.supabase_project(context(Recorder({"db_password": "x"})))


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
    respx.get(url__startswith=f"{PROJECT}/rest/v1/").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(NeedsHuman) as err:
        supabase.data_api_off(context(Recorder({"supabase_ref": REF})))
    assert "Dashboard → zm-pilot → Integrations → Data API → off" in str(err.value)


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
    assert list(STEPS) == [
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


def configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> list[tuple[str, Settings]]:
    calls: list[tuple[str, Settings]] = []
    monkeypatch.setattr(provision, "run", lambda dsn, settings: calls.append((dsn, settings)))
    base = {
        "PLATFORM_DATABASE_URL": "postgresql://unused/postgres",
        "SUPABASE_ACCESS_TOKEN": "t",
        "SUPABASE_ORG_ID": "o",
    }
    for var, value in {**base, **env}.items():
        if value:
            monkeypatch.setenv(var, value)
        else:
            monkeypatch.delenv(var, raising=False)
    return calls


def test_tick_provisions_when_configured(monkeypatch) -> None:
    calls = configure(monkeypatch)
    worker_main.provision_pass()
    assert [dsn for dsn, _ in calls] == ["postgresql://unused/postgres"]


@pytest.mark.parametrize("missing", ["PLATFORM_DATABASE_URL", "SUPABASE_ACCESS_TOKEN"])
def test_tick_skips_provisioning_until_configured(monkeypatch, missing: str) -> None:
    calls = configure(monkeypatch, **{missing: ""})
    worker_main.provision_pass()
    assert calls == []


def test_a_failing_provisioning_pass_is_logged(monkeypatch, caplog) -> None:
    configure(monkeypatch)

    def boom(dsn: str, settings: Settings) -> None:
        raise OSError("registry down")

    monkeypatch.setattr(provision, "run", boom)
    worker_main.provision_pass()
    assert caplog.records[-1].getMessage() == "provisioning pass failed"


def test_settings_from_env(monkeypatch) -> None:
    for var, value in {
        "SUPABASE_ACCESS_TOKEN": "t",
        "SUPABASE_ORG_ID": "o",
        "SUPABASE_REGION": "ap-southeast-1",
    }.items():
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("SUPABASE_CLIENT_INSTANCE_SIZE", raising=False)
    monkeypatch.delenv("SUPABASE_PROJECT_PREFIX", raising=False)
    assert Settings.from_env() == Settings("t", "o", "ap-southeast-1", "micro", "zm-")
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN")
    assert Settings.from_env() is None
