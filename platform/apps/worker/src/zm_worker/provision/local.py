"""`zm-worker provision-local` (#31): register a client on local resources, for the isolation
suite and local demos. The same steps as cloud provisioning (S2-02a/b) minus the cloud calls:
the vault migrations (`vault_migrate.apply`), the registry row (as the `registry` and
`operaton_health` steps leave it) and seed-draft (`railway.seed`), plus the `bucket` step on the
Supabase CLI's Storage when one is given (pilot-local, #51). The vault and the engine already run
(dev-up.sh), so nothing else is created; running it again updates the row and keeps the draft.
"""

from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
from sqlalchemy.engine import make_url

from zm_api import secrets as sealed
from zm_api.context import SERVICE_ACCOUNT
from zm_worker import vault_migrate
from zm_worker.dsn import libpq
from zm_worker.provision.railway import seed
from zm_worker.provision.supabase import BUCKET, ensure_bucket

# local slugs whose template isn't named: the two local clients of .env.example
DEFAULT_TEMPLATES = {"pilot-local": "matrix-retail", "qsr-local": "qsr"}


@dataclass(frozen=True)
class LocalStorage:
    """The Supabase CLI stack's Storage (#51), for pilot-local; qsr-local has none (D11)."""

    url: str
    """Where this host and the browser reach it: `http://127.0.0.1:54321`."""
    service_key: str
    """`SERVICE_ROLE_KEY` from `supabase --workdir infra status -o env`."""
    container_url: str | None = None
    """Where the zm-api container reaches it (`http://host.docker.internal:54321`), when the API
    runs there."""


@dataclass(frozen=True)
class LocalClient:
    slug: str
    dsn: str
    """The vault as its superuser (migrations run as it)."""
    operaton_url: str
    template: str
    name: str
    workspace_code: str
    passwords: dict[str, str]
    """Role → password for zm_api and zm_engine."""
    service_password: str
    storage: LocalStorage | None = None


def api_dsn(dsn: str, password: str) -> str:
    """The vault DSN as the API's role: same host, port and database, user zm_api."""
    url = make_url(libpq(dsn)).set(username="zm_api", password=password)
    return url.render_as_string(hide_password=False)


def _template(conn: psycopg.Connection, key: str) -> dict[str, Any]:
    row = conn.execute(
        "select workflow_json from platform.templates where key = %s order by version desc limit 1",
        (key,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no template {key!r} in platform.templates: run `zm-api templates sync`")
    return row[0]


def check_engine(client: LocalClient, http: httpx.Client | None = None) -> None:
    """The `operaton_health` step's sign-in probe: the service account can query tasks and an
    anonymous query is refused, so a wrong password or URL is never registered as ready.
    (`/engine-rest/engine` skips auth, so it's a real query.)"""
    own = http or httpx.Client(timeout=15)
    try:
        tasks = f"{client.operaton_url.rstrip('/')}/task"
        auth = (SERVICE_ACCOUNT, client.service_password)
        signed_in = own.get(tasks, params={"maxResults": 1}, auth=auth)
        if signed_in.status_code != 200:
            raise RuntimeError(f"{SERVICE_ACCOUNT} can't query tasks: {signed_in.status_code}")
        anonymous = own.get(tasks, params={"maxResults": 1})
        if anonymous.status_code != 401:
            raise RuntimeError(f"an anonymous task query answered {anonymous.status_code}, not 401")
    finally:
        if http is None:
            own.close()


def provision(
    platform_dsn: str, client: LocalClient, http: httpx.Client | None = None
) -> list[str]:
    """Migrate, register as ready, seed the draft; with `storage`, create the bucket and seal
    Storage's URLs and key too (#51). Returns the migrations applied this run."""
    with psycopg.connect(libpq(platform_dsn), autocommit=True) as conn:
        workflow = _template(conn, client.template)  # before anything changes
    check_engine(client, http)
    files: dict[str, str] = {}
    if (store := client.storage) is not None:
        with httpx.Client(timeout=15) as own:
            ensure_bucket(http or own, store.url, store.service_key)
        files = {
            "storage_url": store.container_url or store.url,
            "storage_public_url": store.url,
            "service_role_key": store.service_key,
        }
    applied = vault_migrate.apply(client.dsn, client.passwords)
    zm_api_dsn = api_dsn(client.dsn, client.passwords["zm_api"])
    box = sealed.seal(
        {
            "zm_api_dsn": zm_api_dsn,
            "service_account_password": client.service_password,
            **files,
        }
    )
    with psycopg.connect(libpq(platform_dsn), autocommit=True) as conn:
        conn.execute(
            "insert into platform.clients (slug, name, workspace_code, template_key, status,"
            " operaton_url, bucket, secrets) values (%s, %s, %s, %s, 'ready', %s, %s, %s)"
            " on conflict (slug) do update set name = excluded.name,"
            " workspace_code = excluded.workspace_code, template_key = excluded.template_key,"
            " status = 'ready', operaton_url = excluded.operaton_url, bucket = excluded.bucket,"
            " secrets = excluded.secrets, updated_at = now()",
            (
                client.slug,
                client.name,
                client.workspace_code,
                client.template,
                client.operaton_url,
                BUCKET,
                box,
            ),
        )
    seed(zm_api_dsn, workflow, client.template)
    return applied
