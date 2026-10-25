"""Steps 1–5 of a client's provisioning (S2-02a, #24): its own Supabase project.

Management API fields checked on 2026-10-22 against https://api.supabase.com/api/v1-json.
Every step is idempotent: it looks for its earlier work first, and seals a generated secret
before the call that uses it, so a kill between the two can't lose it.
"""

import httpx

from zm_worker import vault_migrate
from zm_worker.provision.runner import (
    POLL_SECONDS,
    STARTUP_TIMEOUT,
    Context,
    NeedsHuman,
    Step,
    password,
)

API = "https://api.supabase.com"
BUCKET = "files"
HEALTHY = "ACTIVE_HEALTHY"
BROKEN = {"INACTIVE", "INIT_FAILED", "RESTORE_FAILED", "PAUSE_FAILED"}
GONE = {"REMOVED", "GOING_DOWN"}
# What the Dashboard's Data API "off" switch writes: PostgREST keeps running but exposes no schema
# (https://supabase.com/docs/guides/troubleshooting/schema-pg_pgrst_no_exposed_schemas-does-not-exist).
NO_EXPOSED_SCHEMAS = "pg_pgrst_no_exposed_schemas"
PROBE_TRIES = 3  # PostgREST reloads its config a little after the PATCH: probe again before asking


def _name(ctx: Context) -> str:
    return ctx.settings.prefix + ctx.slug


def _project_url(ref: str) -> str:
    return f"https://{ref}.supabase.co"


def _dsn(user: str, ref: str, secret: str, host: str) -> str:
    # session pooler on 5432 (prepared statements, multi-statement migrations); user <role>.<ref>
    return f"postgresql://{user}.{ref}:{secret}@{host}:5432/postgres"


def supabase_project(ctx: Context) -> None:
    """Find or create `<prefix><slug>` in the org, then wait until it is ACTIVE_HEALTHY."""
    name = _name(ctx)
    found = ctx.mgmt.get(f"/v1/organizations/{ctx.settings.org}/projects", params={"search": name})
    found.raise_for_status()
    live = [p for p in found.json()["projects"] if p["name"] == name and p["status"] not in GONE]
    if len(live) > 1:
        raise RuntimeError(f"{len(live)} projects named {name}: delete the extra ones by hand")

    if live:
        ref = live[0]["ref"]
        # adopt only a project this client asked for (killed before saving the ref): never a
        # same-named one from elsewhere, e.g. production's while this worker runs for test
        if ref != ctx.secrets.get("supabase_ref") and ctx.secrets.get("project_name") != name:
            raise NeedsHuman(
                f"A project named {name} ({ref}) exists that this client didn't create. "
                f"Dashboard → {name}: delete it if it is a leftover, or use another slug"
            )
    else:
        db_password = ctx.secrets.get("db_password") or password()
        # sealed before the POST: a kill after it must not lose the password, and marks the
        # project as this client's for the re-run
        ctx.seal(db_password=db_password, project_name=name)
        created = ctx.mgmt.post(
            "/v1/projects",
            json={
                "name": name,
                "organization_slug": ctx.settings.org,
                "region": ctx.settings.region,
                "desired_instance_size": ctx.settings.instance_size,
                "db_pass": db_password,
            },
        )
        created.raise_for_status()
        ref = created.json()["ref"]
    ctx.seal(supabase_ref=ref)

    deadline = ctx.clock() + STARTUP_TIMEOUT
    while True:
        project = ctx.mgmt.get(f"/v1/projects/{ref}")
        project.raise_for_status()
        status = project.json()["status"]
        if status == HEALTHY:
            return
        if status in BROKEN:
            raise RuntimeError(f"project {name} ({ref}) is {status}")
        if ctx.clock() >= deadline:
            raise TimeoutError(f"project {name} ({ref}) still {status} after 10 minutes")
        ctx.heartbeat()
        ctx.sleep(POLL_SECONDS)


def vault_migrations(ctx: Context) -> None:
    """Apply infra/supabase/client migrations; zm_api / zm_engine passwords are generated once."""
    ref = ctx.secrets["supabase_ref"]
    pooler = ctx.mgmt.get(f"/v1/projects/{ref}/config/database/pooler")
    pooler.raise_for_status()
    # the host differs per project (aws-0-…, aws-1-…), so it is read, not built
    host = next(c["db_host"] for c in pooler.json() if c["database_type"] == "PRIMARY")
    passwords = {
        role: ctx.secrets.get(f"{role}_password") or password() for role in vault_migrate.ROLES
    }
    ctx.seal(
        pooler_host=host,
        **{f"{role}_password": pw for role, pw in passwords.items()},
        zm_api_dsn=_dsn("zm_api", ref, passwords["zm_api"], host),
    )
    vault_migrate.apply(_dsn("postgres", ref, ctx.secrets["db_password"], host), passwords)


def _key(keys: list[dict], legacy_name: str, new_type: str) -> str:
    for match in (lambda k: k["name"] == legacy_name, lambda k: k["type"] == new_type):
        for key in keys:
            if match(key) and key.get("api_key"):
                return key["api_key"]
    raise RuntimeError(f"project has no {legacy_name} or {new_type} API key")


def data_api_off(ctx: Context) -> None:
    """Expose no schema through PostgREST, then prove it: the config reads back as set, and the
    anon key reads nothing (a table in `app` and in `operaton` included)."""
    ref = ctx.secrets["supabase_ref"]
    keys = ctx.mgmt.get(f"/v1/projects/{ref}/api-keys", params={"reveal": "true"})
    keys.raise_for_status()
    anon = _key(keys.json(), "anon", "publishable")
    ctx.seal(service_role_key=_key(keys.json(), "service_role", "secret"))
    postgrest = f"/v1/projects/{ref}/postgrest"
    ctx.mgmt.patch(postgrest, json={"db_schema": NO_EXPOSED_SCHEMAS}).raise_for_status()

    # the API has no on/off flag: if this didn't take, a person flips it; re-checked every RECHECK
    click = f"Dashboard → {_name(ctx)} → Integrations → Data API → off"
    config = ctx.mgmt.get(postgrest)
    config.raise_for_status()
    if config.json().get("db_schema") != NO_EXPOSED_SCHEMAS:
        raise NeedsHuman(f"PostgREST still exposes {config.json().get('db_schema')!r}. {click}")
    base = f"{_project_url(ref)}/rest/v1/"
    probes = (
        (base, {}),
        (f"{base}schema_migrations", {"Accept-Profile": "app"}),
        (f"{base}act_ge_property", {"Accept-Profile": "operaton"}),
    )
    for tried in range(1, PROBE_TRIES + 1):
        answered = [
            (url, probe.status_code)
            for url, headers in probes
            if (probe := ctx.http.get(url, headers={"apikey": anon, **headers})).is_success
        ]
        if not answered:
            return
        if tried < PROBE_TRIES:
            ctx.sleep(POLL_SECONDS)
    url, status = answered[0]
    raise NeedsHuman(f"The Data API still answers {url} ({status}). {click}")


def _exists(response: httpx.Response) -> bool:
    if response.status_code == 409:
        return True
    try:  # older Storage versions answer a duplicate with 400 and statusCode "409" in the body
        return response.status_code == 400 and response.json().get("statusCode") == "409"
    except ValueError:
        return False


def bucket(ctx: Context) -> None:
    """Private bucket `files` through the project's Storage API."""
    key = ctx.secrets["service_role_key"]
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):  # legacy JWT; new secret keys go only in apikey
        headers["Authorization"] = f"Bearer {key}"
    url = f"{_project_url(ctx.secrets['supabase_ref'])}/storage/v1/bucket"
    created = ctx.http.post(
        url, json={"id": BUCKET, "name": BUCKET, "public": False}, headers=headers
    )
    if _exists(created):  # keep it, but make sure it is private
        ctx.http.put(f"{url}/{BUCKET}", json={"public": False}, headers=headers).raise_for_status()
    else:
        created.raise_for_status()


def registry(ctx: Context) -> None:
    """Plain registry columns; the secrets were sealed by the steps that made them."""
    ctx.register(supabase_ref=ctx.secrets["supabase_ref"], bucket=BUCKET)


# in seq order; S2-02b's steps (operaton_service, …) follow
STEPS: dict[str, Step] = {
    "supabase_project": supabase_project,
    "vault_migrations": vault_migrations,
    "data_api_off": data_api_off,
    "bucket": bucket,
    "registry": registry,
}
