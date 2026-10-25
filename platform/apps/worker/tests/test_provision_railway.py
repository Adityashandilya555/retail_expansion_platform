"""Provisioning steps for the client's Operaton on Railway (S2-02b, #25), GraphQL mocked."""

import base64
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

import zm_worker.__main__ as worker_main
from zm_api import clients as api_clients
from zm_worker import provision
from zm_worker.provision import STEPS, railway, runner
from zm_worker.provision.runner import Context, NeedsHuman, Settings

IMAGE = "ghcr.io/adityashandilya555/zm-operaton:2.1.5-zm.1"
SETTINGS = Settings(
    access_token="sbp_test",
    org="zm-org",
    region="ap-southeast-1",
    instance_size="micro",
    prefix="zm-",
    railway_token="rw-token",
    railway_project="proj",
    railway_environment="env",
    operaton_image=IMAGE,
)
SECRETS = {
    "supabase_ref": "abcdefghijklmnopqrst",
    "pooler_host": "aws-1-ap-southeast-1.pooler.supabase.com",
    "zm_engine_password": "engine-pw",
    "zm_api_dsn": "postgresql://zm_api.abcdefghijklmnopqrst:api-pw@pooler:5432/postgres",
}
BASE = "http://operaton-pilot.railway.internal:8080"


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


class FakeRailway:
    """Just enough of Railway's GraphQL API, with state, to see what a re-run changes."""

    def __init__(self) -> None:
        self.services: dict[str, str] = {}  # name → id
        self.image: str | None = None
        self.variables: dict[str, str] = {}
        self.deployment: dict[str, str] | None = None
        self.domains: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: list[httpx.Headers] = []
        self.logs = [f"line {i}" for i in range(40)]
        self.deploy_errors = 0  # how many deploy calls fail before one goes through

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query, v = body["query"], body["variables"]
        op = next(
            name
            for name in (
                "serviceCreate",
                "serviceInstanceUpdate",
                "serviceInstanceDeployV2",
                "serviceInstance",
                "variableCollectionUpsert",
                "variables",
                "deploymentLogs",
                "serviceDelete",
                "project",
            )
            if name + "(" in query
        )
        self.calls.append((op, v))
        self.headers.append(request.headers)
        data: Any
        if op == "project":
            edges = [{"node": {"id": i, "name": n}} for n, i in self.services.items()]
            data = {"project": {"services": {"edges": edges}}}
        elif op == "serviceCreate":
            self.services[v["input"]["name"]] = "svc-1"
            self.image = v["input"]["source"]["image"]
            data = {"serviceCreate": {"id": "svc-1"}}
        elif op == "serviceInstance":
            data = {
                "serviceInstance": {
                    "source": {"image": self.image},
                    "latestDeployment": self.deployment,
                    "domains": {
                        "serviceDomains": [{"domain": d} for d in self.domains],
                        "customDomains": [],
                    },
                }
            }
        elif op == "serviceInstanceUpdate":
            self.image = v["input"]["source"]["image"]
            data = {"serviceInstanceUpdate": True}
        elif op == "variables":
            data = {"variables": dict(self.variables)}
        elif op == "variableCollectionUpsert":
            self.variables.update(v["input"]["variables"])
            data = {"variableCollectionUpsert": True}
        elif op == "serviceInstanceDeployV2" and self.deploy_errors:
            self.deploy_errors -= 1
            return httpx.Response(502, text="Bad Gateway")
        elif op == "serviceInstanceDeployV2":
            self.deployment = {
                "id": f"dep-{len(self.ops('serviceInstanceDeployV2'))}",
                "status": "SUCCESS",
            }
            data = {"serviceInstanceDeployV2": self.deployment["id"]}
        elif op == "deploymentLogs":
            data = {"deploymentLogs": [{"message": m} for m in self.logs]}
        else:  # serviceDelete
            self.services.clear()
            data = {"serviceDelete": True}
        return httpx.Response(200, json={"data": data})

    def ops(self, name: str) -> list[dict[str, Any]]:
        return [v for op, v in self.calls if op == name]


@pytest.fixture
def fake() -> FakeRailway:
    return FakeRailway()


class Recorder:
    def __init__(self, secrets: dict[str, Any] | None = None) -> None:
        self.secrets: dict[str, Any] = dict(SECRETS if secrets is None else secrets)
        self.columns: dict[str, Any] = {}


def context(
    rec: Recorder,
    settings: Settings = SETTINGS,
    now: list[float] | None = None,
    template: tuple[str, dict[str, Any]] | None = None,
) -> Context:
    clock = now if now is not None else [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    return Context(
        client_id="00000000-0000-0000-0000-000000000001",
        slug="pilot",
        settings=settings,
        secrets=rec.secrets,
        mgmt=httpx.Client(base_url="https://api.supabase.com"),
        http=httpx.Client(),
        persist_secrets=lambda data: rec.secrets.update(data),
        persist_columns=lambda cols: rec.columns.update(cols),
        heartbeat=lambda: None,
        sleep=sleep,
        clock=lambda: clock[0],
        template=lambda: template,
    )


def mock(fake: FakeRailway) -> None:
    respx.post(railway.GRAPHQL).mock(side_effect=fake)


# --- step 6: operaton_service -----------------------------------------------------------------


@respx.mock
def test_creates_the_service_with_env_and_deploys_it_once(fake: FakeRailway) -> None:
    mock(fake)
    rec = Recorder()

    railway.operaton_service(context(rec))

    (created,) = fake.ops("serviceCreate")
    assert created["input"] == {
        "projectId": "proj",
        "environmentId": "env",
        "name": "operaton-pilot",
        "source": {"image": IMAGE},
    }
    (update,) = fake.ops("serviceInstanceUpdate")
    assert update["input"] == {
        "source": {"image": IMAGE},
        "region": "asia-southeast1-eqsg3a",
        "numReplicas": 1,
        "healthcheckPath": "/health",
    }
    password = rec.secrets["service_account_password"]
    assert len(password) == 32
    assert fake.variables == {
        "DB_DRIVER": "org.postgresql.Driver",
        "DB_URL": "jdbc:postgresql://aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
        "?currentSchema=operaton&sslmode=require",
        "DB_USERNAME": "zm_engine.abcdefghijklmnopqrst",
        "DB_PASSWORD": "engine-pw",
        "ZM_ADMIN_USER_ID": "zm-service",
        "ZM_ADMIN_USER_PASSWORD": password,
        "PORT": "8080",
    }
    assert fake.ops("variableCollectionUpsert")[0]["input"]["skipDeploys"] is True
    assert len(fake.ops("serviceInstanceDeployV2")) == 1
    assert rec.columns == {"railway_service_id": "svc-1"}
    # a project token goes in its own header (Railway public API docs)
    assert fake.headers[0]["project-access-token"] == "rw-token"
    assert "authorization" not in fake.headers[0]


@respx.mock
def test_rerun_with_nothing_changed_creates_and_deploys_nothing(fake: FakeRailway) -> None:
    mock(fake)
    rec = Recorder()
    railway.operaton_service(context(rec))
    fake.calls.clear()

    railway.operaton_service(context(rec))

    assert fake.ops("serviceCreate") == []
    assert fake.ops("variableCollectionUpsert") == []
    assert fake.ops("serviceInstanceDeployV2") == []


@respx.mock
def test_a_changed_variable_is_upserted_alone_and_redeployed(fake: FakeRailway) -> None:
    mock(fake)
    rec = Recorder()
    railway.operaton_service(context(rec))
    fake.calls.clear()
    rec.secrets["zm_engine_password"] = "rotated"

    railway.operaton_service(context(rec))

    (upsert,) = fake.ops("variableCollectionUpsert")
    assert upsert["input"]["variables"] == {"DB_PASSWORD": "rotated"}
    assert len(fake.ops("serviceInstanceDeployV2")) == 1


@respx.mock
def test_a_new_image_redeploys(fake: FakeRailway) -> None:
    mock(fake)
    rec = Recorder()
    railway.operaton_service(context(rec))
    fake.image = "ghcr.io/adityashandilya555/zm-operaton:2.1.4-zm.1"  # running an older one
    fake.calls.clear()

    railway.operaton_service(context(rec))

    assert fake.ops("serviceInstanceUpdate")[0]["input"]["source"] == {"image": IMAGE}
    assert len(fake.ops("serviceInstanceDeployV2")) == 1


@respx.mock
def test_a_crashed_deployment_is_deployed_again(fake: FakeRailway) -> None:
    mock(fake)
    rec = Recorder()
    railway.operaton_service(context(rec))
    fake.deployment = {"id": "dep-1", "status": "CRASHED"}
    fake.calls.clear()

    railway.operaton_service(context(rec))

    assert len(fake.ops("serviceInstanceDeployV2")) == 1


@respx.mock
def test_a_public_domain_needs_a_human(fake: FakeRailway) -> None:
    mock(fake)
    fake.services["operaton-pilot"] = "svc-1"
    fake.domains = ["operaton-pilot.up.railway.app"]
    with pytest.raises(NeedsHuman, match="operaton-pilot.up.railway.app"):
        railway.operaton_service(context(Recorder()))
    assert fake.ops("serviceInstanceDeployV2") == []


@respx.mock
def test_bearer_token_type() -> None:
    fake = FakeRailway()
    mock(fake)
    railway.operaton_service(context(Recorder(), replace(SETTINGS, railway_token_type="bearer")))
    assert fake.headers[0]["authorization"] == "Bearer rw-token"
    assert "project-access-token" not in fake.headers[0]


@respx.mock
def test_graphql_errors_raise() -> None:
    respx.post(railway.GRAPHQL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "Not Authorized"}]})
    )
    with pytest.raises(RuntimeError, match="Railway: Not Authorized"):
        railway.operaton_service(context(Recorder()))


def test_missing_railway_settings_are_named() -> None:
    settings = replace(SETTINGS, railway_token=None, railway_project=None)
    with pytest.raises(RuntimeError, match="set RAILWAY_API_TOKEN, RAILWAY_PROJECT_ID on"):
        railway.operaton_service(context(Recorder(), settings))


@respx.mock
def test_operaton_service_needs_the_image(fake: FakeRailway) -> None:
    mock(fake)
    with pytest.raises(RuntimeError, match="set OPERATON_IMAGE"):
        railway.operaton_service(context(Recorder(), replace(SETTINGS, operaton_image=None)))
    assert fake.calls == []


@respx.mock
def test_a_deploy_cut_off_after_the_upsert_still_happens_on_the_rerun(fake: FakeRailway) -> None:
    """Variables went in with skipDeploys, then the deploy call failed: nothing is left to change
    on the re-run, but it must still deploy, or Operaton runs without its DB settings."""
    mock(fake)
    fake.deploy_errors = 1
    rec = Recorder()
    with pytest.raises(httpx.HTTPStatusError):
        railway.operaton_service(context(rec))
    assert rec.secrets["deploy_pending"] is True
    fake.calls.clear()

    railway.operaton_service(context(rec))

    assert fake.ops("variableCollectionUpsert") == []  # already set
    assert len(fake.ops("serviceInstanceDeployV2")) == 1
    assert rec.secrets["deploy_pending"] is False


@respx.mock
def test_railway_errors_never_carry_secrets_into_last_error(fake: FakeRailway) -> None:
    def echo(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "variableCollectionUpsert(" in body["query"]:
            value = body["variables"]["input"]["variables"]["DB_PASSWORD"]
            message = f'Variable "$input" got invalid value "{value}"'
            return httpx.Response(200, json={"errors": [{"message": message}]})
        return fake(request)

    respx.post(railway.GRAPHQL).mock(side_effect=echo)
    ctx = context(Recorder({**SECRETS, "zm_engine_password": "engine-secret-pw"}))
    status, error, _ = runner.attempt(railway.operaton_service, ctx, attempts=0)
    assert status == "pending"
    assert error == 'Railway: Variable "$input" got invalid value "***"'


# --- step 7: operaton_health ------------------------------------------------------------------


def healthy_engine(password: str) -> None:
    respx.get(f"{BASE}/health").mock(
        side_effect=[
            httpx.ConnectError("not resolvable yet"),
            httpx.Response(503, json={"status": "DOWN"}),
            httpx.Response(200, json={"status": "UP"}),
        ]
    )

    expected = "Basic " + base64.b64encode(f"zm-service:{password}".encode()).decode()

    def tasks(request: httpx.Request) -> httpx.Response:
        ok = request.headers.get("authorization") == expected
        return httpx.Response(200, json=[]) if ok else httpx.Response(401)

    respx.get(f"{BASE}/engine-rest/task").mock(side_effect=tasks)


@respx.mock
def test_health_then_auth_both_ways_then_operaton_url() -> None:
    rec = Recorder({**SECRETS, "service_account_password": "svc-pw"})
    healthy_engine("svc-pw")

    railway.operaton_health(context(rec))

    assert rec.columns == {"operaton_url": f"{BASE}/engine-rest"}


@respx.mock
def test_wrong_service_password_fails() -> None:
    healthy_engine("the-real-one")
    with pytest.raises(RuntimeError, match="zm-service can't query tasks: 401"):
        railway.operaton_health(context(Recorder({**SECRETS, "service_account_password": "x"})))


@respx.mock
def test_engine_without_auth_fails() -> None:
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200))
    respx.get(f"{BASE}/engine-rest/task").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(RuntimeError, match="answered 200, not 401"):
        railway.operaton_health(context(Recorder({**SECRETS, "service_account_password": "x"})))


@respx.mock
def test_never_healthy_times_out_with_the_newest_log_lines(fake: FakeRailway) -> None:
    """E.g. a wrong DB password: the engine never starts, and last_error says why."""
    mock(fake)
    fake.services["operaton-pilot"] = "svc-1"
    fake.deployment = {"id": "dep-9", "status": "CRASHED"}
    fake.logs = [
        f"{i:02d} FATAL: password authentication failed for user zm_engine" for i in range(40)
    ]
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    now = [0.0]

    with pytest.raises(TimeoutError) as err:
        railway.operaton_health(context(Recorder(), now=now))

    message = str(err.value)
    assert message.startswith("operaton-pilot not healthy after 10 minutes. Last log lines:")
    assert message.endswith("39 FATAL: password authentication failed for user zm_engine")
    assert "00 FATAL" not in message  # only the newest lines that fit
    assert len(message) < 1000  # last_error keeps 1000 characters
    assert fake.ops("deploymentLogs") == [{"d": "dep-9"}]
    assert 600 <= now[0] < 700


@respx.mock
def test_a_long_last_log_line_is_kept_cut_to_fit(fake: FakeRailway) -> None:
    mock(fake)
    fake.services["operaton-pilot"] = "svc-1"
    fake.deployment = {"id": "dep-9", "status": "CRASHED"}
    fake.logs = ["short", "org.springframework.beans.factory.BeanCreationException: " + "x" * 2000]
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TimeoutError) as err:
        railway.operaton_health(context(Recorder()))
    assert "BeanCreationException" in str(err.value)
    assert len(str(err.value)) < 1000


# --- step 8: seed_draft -----------------------------------------------------------------------


def test_seed_draft_copies_the_template_and_marks_ready(monkeypatch) -> None:
    seeded: list[tuple[str, dict[str, Any], str]] = []
    monkeypatch.setattr(railway, "seed", lambda dsn, w, k: seeded.append((dsn, w, k)))
    rec = Recorder()
    workflow = {"version": 1, "org": {}}

    railway.seed_draft(context(rec, template=("qsr", workflow)))

    assert seeded == [(SECRETS["zm_api_dsn"], workflow, "qsr")]
    assert rec.columns == {"status": "ready"}


def test_seed_draft_without_a_template_row_fails() -> None:
    with pytest.raises(RuntimeError, match="no row in platform.templates"):
        railway.seed_draft(context(Recorder(), template=None))


# --- deprovision ------------------------------------------------------------------------------


@respx.mock
def test_deprovision_deletes_the_service_and_the_project(fake: FakeRailway) -> None:
    mock(fake)
    fake.services["operaton-pilot"] = "svc-1"
    delete = respx.delete(f"https://api.supabase.com/v1/projects/{SECRETS['supabase_ref']}").mock(
        return_value=httpx.Response(200, json={})
    )
    rec = Recorder()

    deleted = railway.deprovision(context(rec))

    assert fake.ops("serviceDelete") == [{"id": "svc-1", "e": "env"}]
    assert delete.called
    assert deleted == [
        "Railway service operaton-pilot",
        f"Supabase project {SECRETS['supabase_ref']}",
    ]
    assert rec.columns == {"status": "suspended"}


@respx.mock
def test_deprovision_needs_no_image(fake: FakeRailway) -> None:
    mock(fake)
    respx.delete(url__startswith="https://api.supabase.com/v1/projects/").mock(
        return_value=httpx.Response(404)
    )
    assert railway.deprovision(context(Recorder(), replace(SETTINGS, operaton_image=None))) == []


@respx.mock
def test_deprovision_twice_deletes_nothing_more(fake: FakeRailway) -> None:
    mock(fake)
    respx.delete(url__startswith="https://api.supabase.com/v1/projects/").mock(
        return_value=httpx.Response(404)
    )
    assert railway.deprovision(context(Recorder())) == []


def test_deprovision_cli_needs_yes(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(provision, "deprovision", lambda dsn, s, slug: calls.append(slug) or [])
    for var, value in {
        "PLATFORM_DATABASE_URL": "postgresql://unused/postgres",
        "SUPABASE_ACCESS_TOKEN": "t",
        "SUPABASE_ORG_ID": "o",
        "SUPABASE_PROJECT_PREFIX": "zm-test-",
    }.items():
        monkeypatch.setenv(var, value)

    with pytest.raises(SystemExit):
        worker_main.main(["deprovision", "pilot"])
    assert "add --yes" in capsys.readouterr().err
    assert calls == []

    worker_main.main(["deprovision", "pilot", "--yes"])
    assert calls == ["pilot"]


# --- order ------------------------------------------------------------------------------------


def test_worker_knows_every_step_the_api_queues_in_order() -> None:
    assert list(STEPS) == list(api_clients.STEPS)


def test_railway_settings_from_env(monkeypatch) -> None:
    for var, value in {
        "SUPABASE_ACCESS_TOKEN": "t",
        "SUPABASE_ORG_ID": "o",
        "SUPABASE_PROJECT_PREFIX": "zm-test-",
        "RAILWAY_API_TOKEN": "rw",
        "RAILWAY_API_TOKEN_TYPE": "bearer",
        "RAILWAY_PROJECT_ID": "p",
        "RAILWAY_ENVIRONMENT_ID": "e",
        "OPERATON_IMAGE": IMAGE,
    }.items():
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("RAILWAY_REGION", raising=False)
    s = Settings.from_env()
    assert s is not None
    assert (s.railway_token, s.railway_token_type, s.railway_project, s.railway_environment) == (
        "rw",
        "bearer",
        "p",
        "e",
    )
    assert (s.railway_region, s.operaton_image) == ("asia-southeast1-eqsg3a", IMAGE)
