"""Steps 6–8 of a client's provisioning (S2-02b, #25): its own Operaton on Railway, its health
over the private network, and its first draft.

Railway GraphQL fields checked on 2026-10-24 by introspecting https://backboard.railway.com/graphql/v2.
A project token authenticates with `Project-Access-Token`, account/workspace tokens with Bearer
(https://docs.railway.com/reference/public-api).
"""

from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from zm_api.context import SERVICE_ACCOUNT
from zm_worker.dsn import libpq
from zm_worker.provision.runner import (
    POLL_SECONDS,
    STARTUP_TIMEOUT,
    Context,
    NeedsHuman,
    Settings,
    Step,
    password,
)

GRAPHQL = "https://backboard.railway.com/graphql/v2"
PORT = 8080  # Operaton Run's port; Railway's health check probes $PORT (infra/operaton/default.yml)
# a deployment in one of these won't become healthy by waiting, so the step deploys again
DEAD = {"FAILED", "CRASHED", "REMOVED", "SKIPPED"}
LOG_CHARS = 800  # newest deployment log lines kept for last_error (which keeps 1000 characters)

# Services belong to the project; each environment has its own instances (D16: test and
# production share zm-platform). Look a client's engine up in this worker's environment only.
FIND = """query($e: String!) { environment(id: $e) {
    serviceInstances { edges { node { serviceId serviceName } } } } }"""
FIND_PROJECT = "query($p: String!) { project(id: $p) { services { edges { node { id name } } } } }"
CREATE = "mutation($input: ServiceCreateInput!) { serviceCreate(input: $input) { id } }"
INSTANCE = """query($s: String!, $e: String!) { serviceInstance(serviceId: $s, environmentId: $e) {
    source { image } latestDeployment { id status }
    domains { serviceDomains { domain } customDomains { domain } } } }"""
UPDATE = """mutation($s: String!, $e: String!, $input: ServiceInstanceUpdateInput!) {
    serviceInstanceUpdate(serviceId: $s, environmentId: $e, input: $input) }"""
VARIABLES = """query($p: String!, $e: String!, $s: String!) {
    variables(projectId: $p, environmentId: $e, serviceId: $s) }"""
UPSERT = (
    "mutation($input: VariableCollectionUpsertInput!) { variableCollectionUpsert(input: $input) }"
)
DEPLOY = """mutation($s: String!, $e: String!) {
    serviceInstanceDeployV2(serviceId: $s, environmentId: $e) }"""
LOGS = "query($d: String!) { deploymentLogs(deploymentId: $d, limit: 30) { message } }"
DELETE = "mutation($id: String!, $e: String) { serviceDelete(id: $id, environmentId: $e) }"


def _railway(settings: Settings) -> tuple[str, str, dict[str, str]]:
    """(project, environment, auth headers), or an error naming what is unset."""
    values = {
        "RAILWAY_API_TOKEN": settings.railway_token,
        "RAILWAY_PROJECT_ID": settings.railway_project,
        "RAILWAY_ENVIRONMENT_ID": settings.railway_environment,
    }
    missing = [var for var, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"set {', '.join(missing)} on the worker")
    token = str(settings.railway_token)
    auth = (
        {"Project-Access-Token": token}
        if settings.railway_token_type == "project"
        else {"Authorization": f"Bearer {token}"}
    )
    return str(settings.railway_project), str(settings.railway_environment), auth


def gql(http: httpx.Client, auth: dict[str, str], query: str, **variables: Any) -> dict[str, Any]:
    response = http.post(GRAPHQL, json={"query": query, "variables": variables}, headers=auth)
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):  # GraphQL reports failures with 200 and an errors list
        raise RuntimeError("Railway: " + "; ".join(e.get("message", "?") for e in body["errors"]))
    return body["data"]


def _service_name(ctx: Context) -> str:
    return f"operaton-{ctx.slug}"


def _base_url(ctx: Context) -> str:
    # Railway's private network has no TLS and never leaves the project; the service has no
    # public domain (operaton_service refuses one), so plain HTTP is the only way in.
    return f"http://{_service_name(ctx)}.railway.internal:{PORT}"  # NOSONAR python:S5332


def _find(http: httpx.Client, auth: dict[str, str], env: str, name: str) -> str | None:
    """`name`'s service id if it has an instance in environment `env`."""
    edges = gql(http, auth, FIND, e=env)["environment"]["serviceInstances"]["edges"]
    return next((e["node"]["serviceId"] for e in edges if e["node"]["serviceName"] == name), None)


def _elsewhere(http: httpx.Client, auth: dict[str, str], project: str, name: str) -> bool:
    """Whether the project has `name` at all (then in another environment, if not in ours)."""
    edges = gql(http, auth, FIND_PROJECT, p=project)["project"]["services"]["edges"]
    return any(e["node"]["name"] == name for e in edges)


def operaton_service(ctx: Context) -> None:
    """Find or create `operaton-<slug>`, set its env, and deploy when something changed.

    `deploy_pending` is sealed before any change and cleared only after the deploy went through,
    so a run cut off between the two (variables set with skipDeploys, deploy call failed) still
    deploys on the re-run, which otherwise would see nothing left to change."""
    project, env, auth = _railway(ctx.settings)
    image = ctx.settings.operaton_image
    if not image:
        raise RuntimeError("set OPERATON_IMAGE on the worker")
    name = _service_name(ctx)
    service = _find(ctx.http, auth, env, name)
    if service is None and _elsewhere(ctx.http, auth, project, name):
        # a slug lives in one environment only (e.g. a demo rehearsed on test under this slug)
        raise NeedsHuman(
            f"{name} already exists in another environment of zm-platform. Give this client a "
            f"slug not used there, or delete {name} in that environment (Railway → zm-platform "
            f"→ that environment → {name} → Settings → Delete service)"
        )
    if service is None:
        ctx.seal(deploy_pending=True)  # Railway's first deployment has none of our variables
        created = gql(
            ctx.http,
            auth,
            CREATE,
            input={
                "projectId": project,
                "environmentId": env,
                "name": name,
                "source": {"image": image},
            },
        )
        service = created["serviceCreate"]["id"]
    ctx.register(railway_service_id=service)

    admin_password = ctx.secrets.get("service_account_password") or password()
    ctx.seal(service_account_password=admin_password)
    instance = gql(ctx.http, auth, INSTANCE, s=service, e=env)["serviceInstance"]
    domains = instance["domains"]
    public = [d["domain"] for d in domains["serviceDomains"] + domains["customDomains"]]
    if public:  # reachable from the internet: a person removes it, the step re-checks
        raise NeedsHuman(
            f"{name} has public domains {', '.join(public)}. "
            f"Railway → zm-platform → {name} → Settings → Networking → delete each domain"
        )

    s = ctx.secrets
    wanted = {
        "DB_DRIVER": "org.postgresql.Driver",
        # TLS: Railway reaches the Supabase pooler over the public internet
        "DB_URL": f"jdbc:postgresql://{s['pooler_host']}:5432/postgres"
        "?currentSchema=operaton&sslmode=require",
        "DB_USERNAME": f"zm_engine.{s['supabase_ref']}",
        "DB_PASSWORD": s["zm_engine_password"],
        "ZM_ADMIN_USER_ID": SERVICE_ACCOUNT,
        "ZM_ADMIN_USER_PASSWORD": admin_password,
        "PORT": str(PORT),
    }
    current = gql(ctx.http, auth, VARIABLES, p=project, e=env, s=service)["variables"]
    changed = {k: v for k, v in wanted.items() if current.get(k) != v}
    latest = instance["latestDeployment"]
    if (
        changed
        or (instance["source"] or {}).get("image") != image
        or latest is None
        or latest["status"] in DEAD
    ):
        ctx.seal(deploy_pending=True)

    gql(
        ctx.http,
        auth,
        UPDATE,
        s=service,
        e=env,
        input={
            "source": {"image": image},
            "region": ctx.settings.railway_region,
            "numReplicas": 1,
            "healthcheckPath": "/health",
        },
    )
    if changed:
        gql(
            ctx.http,
            auth,
            UPSERT,
            input={
                "projectId": project,
                "environmentId": env,
                "serviceId": service,
                "variables": changed,
                "skipDeploys": True,  # one deploy below, not one per change
            },
        )
    if ctx.secrets.get("deploy_pending"):
        gql(ctx.http, auth, DEPLOY, s=service, e=env)
        ctx.seal(deploy_pending=False)


def _log_tail(ctx: Context) -> str:
    """The newest deployment log lines that fit in LOG_CHARS, oldest first; '' if unavailable."""
    try:
        project, env, auth = _railway(ctx.settings)
        service = _find(ctx.http, auth, project, _service_name(ctx))
        if service is None:
            return ""
        latest = gql(ctx.http, auth, INSTANCE, s=service, e=env)["serviceInstance"][
            "latestDeployment"
        ]
        if latest is None:
            return ""
        lines = [
            line["message"] for line in gql(ctx.http, auth, LOGS, d=latest["id"])["deploymentLogs"]
        ]
    except (httpx.HTTPError, RuntimeError, KeyError, TypeError):
        return ""
    kept: list[str] = []
    for line in reversed(lines):
        if kept and sum(len(k) + 1 for k in kept) + len(line) > LOG_CHARS:
            break
        kept.append(line[:LOG_CHARS])  # the newest line always, cut to fit (a stack trace)
        if len(line) >= LOG_CHARS:
            break
    return "\n".join(reversed(kept))


def operaton_health(ctx: Context) -> None:
    """Wait for /health over the private network, then prove auth works both ways."""
    base = _base_url(ctx)
    deadline = ctx.clock() + STARTUP_TIMEOUT
    while True:
        try:
            if ctx.http.get(f"{base}/health").status_code == 200:
                break
        except httpx.TransportError:  # not resolvable or not listening yet
            pass
        if ctx.clock() >= deadline:
            tail = _log_tail(ctx)
            raise TimeoutError(
                f"{_service_name(ctx)} not healthy after 10 minutes. Last log lines:\n{tail}"
                if tail
                else f"{_service_name(ctx)} not healthy after 10 minutes (no deployment logs)"
            )
        ctx.heartbeat()
        ctx.sleep(POLL_SECONDS)

    # /engine-rest/engine skips auth (ProcessEngineAuthenticationFilter), so a real query
    tasks = f"{base}/engine-rest/task"
    signed_in = ctx.http.get(
        tasks,
        params={"maxResults": 1},
        auth=(SERVICE_ACCOUNT, ctx.secrets["service_account_password"]),
    )
    if signed_in.status_code != 200:
        raise RuntimeError(f"{SERVICE_ACCOUNT} can't query tasks: {signed_in.status_code}")
    anonymous = ctx.http.get(tasks, params={"maxResults": 1})
    if anonymous.status_code != 401:
        raise RuntimeError(f"unauthenticated task query answered {anonymous.status_code}, not 401")
    ctx.register(operaton_url=f"{base}/engine-rest")


def seed(dsn: str, workflow: dict[str, Any], template_key: str) -> None:
    """The first draft, unless the client already has one (a re-run keeps edits)."""
    with psycopg.connect(libpq(dsn), autocommit=True) as conn:
        conn.execute(
            "insert into app.drafts (id, workflow, template_key) values (1, %s, %s)"
            " on conflict (id) do nothing",
            (Jsonb(workflow), template_key),
        )


def seed_draft(ctx: Context) -> None:
    """Copy the client's template into app.drafts; then it is ready (publishing stays a click)."""
    template = ctx.template()
    if template is None:
        raise RuntimeError("the client's template_key has no row in platform.templates")
    key, workflow = template
    seed(ctx.secrets["zm_api_dsn"], workflow, key)
    ctx.register(status="ready")


def deprovision(ctx: Context) -> list[str]:
    """Delete the client's Railway service and Supabase project (test clients; never automatic).
    The client is suspended first, so the worker stops claiming its steps."""
    ctx.register(status="suspended")
    deleted: list[str] = []
    project, env, auth = _railway(ctx.settings)
    service = _find(ctx.http, auth, env, _service_name(ctx))
    if service is not None:
        gql(ctx.http, auth, DELETE, id=service, e=env)
        deleted.append(f"Railway service {_service_name(ctx)}")
    ref = ctx.secrets.get("supabase_ref")
    if ref:
        # test and production share one org token (D16): delete only a project of our prefix
        found = ctx.mgmt.get(f"/v1/projects/{ref}")
        if found.status_code != 404:
            found.raise_for_status()
            project_name = str(found.json().get("name", ""))
            if not project_name.startswith(ctx.settings.prefix):
                raise RuntimeError(
                    f"Supabase project {ref} is {project_name!r}, not a {ctx.settings.prefix}"
                    " project: not deleting it"
                )
        response = ctx.mgmt.delete(f"/v1/projects/{ref}")
        if response.status_code != 404:
            response.raise_for_status()
            deleted.append(f"Supabase project {ref}")
    return deleted


# in seq order, after supabase.STEPS
STEPS: dict[str, Step] = {
    "operaton_service": operaton_service,
    "operaton_health": operaton_health,
    "seed_draft": seed_draft,
}
