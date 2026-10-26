"""Two local clients for the isolation suite (#31): `pytest -m isolation` after scripts/dev-up.sh.

- pilot-local: Matrix Retail on the Supabase CLI vault (:54322) and operaton-pilot;
- qsr-local: QSR on pg-iso (:5434) and operaton-iso, each with credentials of its own.

Both are registered with `zm-worker provision-local` (the provisioning steps minus the cloud
calls), published as v1, given demo users, and given one case each, so each side has real ids
for the other to probe. Vault rows and deployments are removed afterwards; the registry rows
stay (provision-local brings them back to the same state).
"""

import argparse
import asyncio
import importlib.util
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

import zm_worker.__main__ as worker
from zm_api import templates
from zm_api.context import PlatformRegistry, Resolver
from zm_api.main import create_app
from zm_api.publish import publish
from zm_api.releases import cache_clear
from zm_api.users import seed_users


def _port(var: str, default: int) -> int:
    return int(os.environ.get(var, default))


def _vault_port(url_var: str, default: int) -> int:
    """The vault's port from LOCAL_CLIENT_DATABASE_URL / LOCAL_ISO_DATABASE_URL (S1-01)."""
    url = os.environ.get(url_var)
    return make_url(url).port or default if url else default


def _engine(url_var: str, port_var: str, default: int) -> str:
    """OPERATON_URL / OPERATON_ISO_URL, else localhost on the compose port."""
    return os.environ.get(url_var) or f"http://localhost:{_port(port_var, default)}/engine-rest"


PLATFORM_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:{_port('PG_PLATFORM_PORT', 5433)}/postgres"
)
JWT_SECRET = "isolation-jwt-secret-0123456789abcdef0123456789abcdef"
KEY = os.environ.get("SECRETS_ENCRYPTION_KEY") or Fernet.generate_key().decode()
INI = Path(__file__).resolve().parents[2] / "apps/api/alembic.ini"
USER_DOMAIN = "isolation.test"
DEMO = Path(__file__).resolve().parents[2] / "scripts/demo_s1.py"


@dataclass(frozen=True)
class Side:
    """One local client and everything the checks need about it."""

    name: str
    slug: str
    vault_port: int
    engine: str
    api_password: str
    engine_password: str
    service_password: str
    template: str
    users: dict[str, tuple[str, int]]
    """Role → (department, level) of its demo users."""

    @property
    def code(self) -> str:
        return self.slug.upper()

    @property
    def key_prefix(self) -> str:
        return "zm_" + self.slug.replace("-", "_") + "__"

    def superuser(self) -> str:
        return f"postgresql://postgres:postgres@localhost:{self.vault_port}/postgres"

    def role_dsn(self, role: str, password: str) -> str:
        return f"postgresql://{role}:{password}@localhost:{self.vault_port}/postgres"


PILOT = Side(
    name="pilot",
    slug=os.environ.get("LOCAL_CLIENT_SLUG", "pilot-local"),
    vault_port=_vault_port("LOCAL_CLIENT_DATABASE_URL", 54322),
    engine=_engine("OPERATON_URL", "OPERATON_PILOT_PORT", 8080),
    api_password=os.environ.get("ZM_API_DB_PASSWORD", "zm-api-local"),
    engine_password=os.environ.get("ZM_ENGINE_DB_PASSWORD", "zm-engine-local"),
    service_password=os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only"),
    template="matrix-retail",
    users={"bd1": ("bd", 1), "bd2": ("bd", 2), "legal1": ("legal", 1), "legal2": ("legal", 2)},
)
QSR = Side(
    name="qsr",
    slug=os.environ.get("LOCAL_ISO_CLIENT_SLUG", "qsr-local"),
    vault_port=_vault_port("LOCAL_ISO_DATABASE_URL", _port("PG_ISO_PORT", 5434)),
    engine=_engine("OPERATON_ISO_URL", "OPERATON_ISO_PORT", 8081),
    api_password=os.environ.get("ZM_ISO_API_DB_PASSWORD", "zm-api-iso-local"),
    engine_password=os.environ.get("ZM_ISO_ENGINE_DB_PASSWORD", "zm-engine-iso-local"),
    service_password=os.environ.get("ZM_ISO_ADMIN_USER_PASSWORD", "zm-iso-local-only"),
    template="qsr",
    users={"bd1": ("bd", 1)},
)
START = {
    "matrix-retail": {
        "site_name": "Indiranagar",
        "city": "Bengaluru",
        "address": "12th Main",
        "store_model": "kiosk",
    },
}


def env_vars() -> dict[str, str]:
    return {
        "PLATFORM_DATABASE_URL": PLATFORM_URL,
        "SECRETS_ENCRYPTION_KEY": KEY,
        "JWT_SECRET": JWT_SECRET,
    }


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in env_vars().items():
        monkeypatch.setenv(k, v)


def vault_sql(side: Side, statement: str, *params: Any) -> list[tuple]:
    with psycopg.connect(side.superuser(), autocommit=True) as conn:
        cur = conn.execute(statement, params)
        return cur.fetchall() if cur.description else []


def engine_client(side: Side, password: str | None = None) -> httpx.Client:
    auth = ("zm-service", password or side.service_password)
    return httpx.Client(base_url=side.engine, auth=auth, timeout=15)


def clear(side: Side) -> None:
    for table in ("app.cases", "app.case_counters", "app.releases", "app.drafts"):
        vault_sql(side, f"delete from {table}")
    vault_sql(side, "delete from app.users where email like %s", f"%@{USER_DOMAIN}")
    with engine_client(side) as op:
        for d in op.get("deployment", params={"name": f"{side.slug}-release"}).json():
            op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def provision_local(side: Side) -> None:
    """`zm-worker provision-local`, with that client's own passwords."""
    prefix = "ZM_ISO_" if side is QSR else "ZM_"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LOCAL_ISO_CLIENT_SLUG", QSR.slug)
        mp.setenv(f"{prefix}API_DB_PASSWORD", side.api_password)
        mp.setenv(f"{prefix}ENGINE_DB_PASSWORD", side.engine_password)
        mp.setenv(f"{prefix}ADMIN_USER_PASSWORD", side.service_password)
        worker.main(
            [
                "provision-local",
                side.slug,
                "--dsn",
                side.superuser(),
                "--operaton-url",
                side.engine,
                "--template",
                side.template,
            ]
        )


def token(side: Side, user_id: uuid.UUID, **claims: Any) -> str:
    payload = {"sub": str(user_id), "kind": "client", "client": side.slug}
    payload |= {"exp": int(time.time()) + 600, **claims}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@dataclass
class Client:
    side: Side
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    passwords: dict[str, str] = field(default_factory=dict)
    case: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""

    def email(self, role: str) -> str:
        return f"{role}-{self.side.name}@{USER_DOMAIN}"

    def auth(self, role: str = "bd1") -> dict[str, str]:
        return {"Authorization": f"Bearer {token(self.side, self.users[role])}"}

    def foreign_auth(self, other: "Client", role: str = "bd1") -> dict[str, str]:
        """A token for this client that names the other client's user."""
        return {"Authorization": f"Bearer {token(self.side, other.users[role])}"}


@dataclass
class World:
    http: TestClient
    pilot: Client
    qsr: Client


async def _publish_and_seed(clients: list[Client]) -> None:
    registry = PlatformRegistry()
    resolver = Resolver(registry)
    try:
        for c in clients:
            ctx = await resolver.resolve(c.side.slug)
            assert ctx is not None, c.side.slug
            await publish(ctx, registry.engine(), None)
            people = [
                {"email": c.email(r), "name": r.title(), "department": d, "level": n}
                for r, (d, n) in c.side.users.items()
            ]
            for seeded in await seed_users(ctx, people):
                role = seeded.email.split("-")[0]
                c.users[role], c.passwords[role] = seeded.id, seeded.password or ""
    finally:
        await resolver.aclose()


async def _sync_templates() -> None:
    engine = create_async_engine(PLATFORM_URL)
    try:
        async with engine.begin() as conn:
            await templates.sync(conn, templates.load_templates())
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def world() -> Iterator[World]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        asyncio.run(_sync_templates())
        for side in (PILOT, QSR):
            clear(side)
        cache_clear()
        for side in (PILOT, QSR):
            provision_local(side)
        pilot, qsr = Client(PILOT), Client(QSR)
        asyncio.run(_publish_and_seed([pilot, qsr]))
        try:
            with TestClient(create_app()) as http:
                for c in (pilot, qsr):
                    c.case = _start_case(http, c)
                    c.task_id = _any_task(c)
                yield World(http, pilot, qsr)
        finally:
            for side in (PILOT, QSR):
                clear(side)
            cache_clear()


def demo_driver() -> ModuleType:
    """platform/scripts/demo_s1.py, loaded from its path (scripts/ isn't a package)."""
    spec = importlib.util.spec_from_file_location("demo_s1", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _start_case(http: TestClient, c: Client) -> dict[str, Any]:
    if c.side.template in START:
        values = START[c.side.template]
    else:  # the template's own start form, filled the way the demo driver fills forms
        schema = vault_sql(
            c.side, "select forms -> 'start:store_launch' -> 'schema' from app.releases"
        )
        values = demo_driver().fill(schema[0][0])
    body = {"flow": "store_launch", "values": values}
    response = http.post("/app/cases", json=body, headers=c.auth())
    assert response.status_code == 201, response.text
    return response.json()


def _any_task(c: Client) -> str:
    """An open task of the client's case, whoever it's for: read from that client's engine."""
    with engine_client(c.side) as op:
        found = op.post("task", json={"processInstanceBusinessKey": c.case["code"]}).json()
    assert found, f"{c.side.slug} has no open task for {c.case['code']}"
    return found[0]["id"]
