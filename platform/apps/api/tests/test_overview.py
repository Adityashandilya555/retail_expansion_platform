"""Owner dashboard (#47): per client, account counts, cases in progress per flow, the active
release and the health of its Operaton and vault, cached for 60 s.

Unit tests build each client from its registry row, faking only its vault engine and answering
its Operaton with an httpx MockTransport; the `integration` test reads the pilot vault and engine
of the local stack (scripts/dev-up.sh).
"""

import argparse
import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy.pool import NullPool

from zm_api import clients, context
from zm_api.context import Resolver
from zm_api.main import create_app
from zm_api.operaton import OperatonClient
from zm_api.registry import Client

from .conftest import bearer, client_row, token
from .test_publish import INI, PLATFORM_URL, engine_client, platform_sql, register, vault_sql

URL = "/owner/clients/overview"
PUBLISHED = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


class Result:
    """Just enough of SQLAlchemy's Result for the overview's queries."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "Result":
        return self

    def one(self) -> dict[str, Any]:
        return self.rows[0]

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)


@dataclass
class Db:
    """A fake database: the platform's client rows, or one client's vault."""

    clients: list[Client] = field(default_factory=list)
    users: tuple[int, int, int] = (0, 0, 0)
    """active, invited, deactivated"""
    release: dict[str, Any] | None = None
    down: bool = False
    queries: int = 0

    def engine(self) -> "Db":  # as app.state.platform
        return self

    @asynccontextmanager
    async def connect(self) -> AsyncIterator["Db"]:
        self.queries += 1
        if self.down:
            raise OSError("connection refused")
        yield self

    async def execute(self, statement: Any, params: Any = None) -> Result:
        sql = str(statement)
        if "platform.clients" in sql:
            return Result([asdict(c) for c in self.clients])
        if "app.users" in sql:
            return Result(
                [dict(zip(("active", "invited", "deactivated"), self.users, strict=True))]
            )
        if "app.releases" in sql:
            return Result([self.release] if self.release else [])
        assert sql.strip() == "select 1", sql
        return Result([{"one": 1}])


@dataclass
class VaultEngine:
    """What `context.create_async_engine` hands out here: an engine on a client's fake vault."""

    vault: Db
    pool: dict[str, Any]
    disposed: bool = False

    def connect(self) -> Any:
        return self.vault.connect()

    async def dispose(self) -> None:
        self.disposed = True


@dataclass
class Engine:
    """A client's Operaton: Operaton Run's /health beside /engine-rest, and instance counts."""

    counts: dict[str, int] = field(default_factory=dict)
    up: bool = True
    hang: bool = False
    breaks: bool = False
    calls: list[str] = field(default_factory=list)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.url.path}?{request.url.query.decode()}")
        if self.breaks:  # as httpx does on a closed client
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        if self.hang:
            await asyncio.sleep(5)
        if request.url.path == "/health":
            status = "UP" if self.up else "DOWN"
            return httpx.Response(200 if self.up else 503, json={"status": status})
        if self.up and request.url.path == "/engine-rest/process-instance/count":
            key = request.url.params["processDefinitionKey"]
            return httpx.Response(200, json={"count": self.counts[key]})
        return httpx.Response(503, json={"type": "Down", "message": "engine down"})


def release(version: int, process_keys: dict[str, str]) -> dict[str, Any]:
    """An `app.releases` row as `releases.active_release` reads it."""
    return {
        "version": version,
        "deployment_id": f"dep-{version}",
        "workflow": {},
        "forms": {},
        "ui": {},
        "routing_vars": {},
        "process_keys": process_keys,
        "blocks_used": {},
        "sha256": "0" * 64,
        "published_by": None,
        "published_at": PUBLISHED,
        "active": True,
    }


@dataclass
class World:
    api: TestClient
    rows: dict[str, Client]
    platform: Db
    vaults: dict[str, Db]
    engines: dict[str, Engine]
    now: list[float]
    resolver: Resolver
    dbs: list[VaultEngine]
    """Every vault engine opened, in order."""
    operatons: list[OperatonClient]
    """Every Operaton client opened, in order."""

    def overview(self) -> dict[str, dict[str, Any]]:
        response = self.api.get(URL, headers=bearer(token("owner", client=None)))
        assert response.status_code == 200, response.text
        return {c["slug"]: c for c in response.json()}


@pytest.fixture
def world(rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch) -> World:
    """Matrix Retail (pilot-local) with 5 active, 1 invited and 1 deactivated users and v3
    running two flows; qsr-local with its own; new-local still provisioning. The Resolver keeps
    one client, fewer than are ready."""
    rows = {
        **rows,
        "new-local": client_row("new-local", "", "new-db", "pw", status="provisioning"),
    }
    vaults = {
        "pilot-local": Db(
            users=(5, 1, 1),
            release=release(
                3, {"store_launch": "zm_pilot__store_launch", "refit": "zm_pilot__refit"}
            ),
        ),
        "qsr-local": Db(users=(2, 0, 0), release=release(1, {"qsr_launch": "zm_qsr__launch"})),
    }
    engines = {
        "pilot-local": Engine(counts={"zm_pilot__store_launch": 4, "zm_pilot__refit": 0}),
        "qsr-local": Engine(counts={"zm_qsr__launch": 2}),
    }
    now = [1000.0]
    monkeypatch.setattr(clients, "clock", lambda: now[0])
    dbs: list[VaultEngine] = []
    operatons: list[OperatonClient] = []

    def create_async_engine(url: sqlalchemy.URL, **pool: Any) -> VaultEngine:
        # conftest's DSN carries the slug in its password: `zm_api:db-<slug>@…`
        dbs.append(VaultEngine(vaults[str(url.password).removeprefix("db-")], pool))
        return dbs[-1]

    def operaton_client(base_url: str, *, auth: tuple[str, str], client: str) -> OperatonClient:
        transport = httpx.MockTransport(engines[client].handle)
        operatons.append(OperatonClient(base_url, auth=auth, client=client, transport=transport))
        return operatons[-1]

    monkeypatch.setattr(context, "create_async_engine", create_async_engine)
    monkeypatch.setattr(context, "OperatonClient", operaton_client)

    async def lookup(slug: str) -> Client | None:
        return rows.get(slug)

    resolver = Resolver(lookup, max_clients=1)
    app = create_app(resolver=resolver)
    app.state.platform = Db(clients=list(rows.values()))
    return World(
        TestClient(app), rows, app.state.platform, vaults, engines, now, resolver, dbs, operatons
    )


def test_probing_more_clients_than_the_resolver_keeps_leaves_its_clients_alone(
    world: World,
) -> None:
    """A request is using pilot-local's context, the one the Resolver keeps, while the dashboard
    probes pilot-local and qsr-local: each probe gets its own short-lived vault engine and
    Operaton client, so the Resolver evicts and closes nothing."""
    held = asyncio.run(world.resolver.context(world.rows["pilot-local"]))
    world.overview()
    assert asyncio.run(world.resolver.context(world.rows["pilot-local"])) is held
    assert not held.operaton.http.is_closed and not held.db.disposed  # type: ignore[attr-defined]
    dbs, operatons = world.dbs[1:], world.operatons[1:]  # the probes'
    assert sorted(db.vault.users for db in dbs) == [(2, 0, 0), (5, 1, 1)]  # qsr, pilot
    assert all(db.pool == {"poolclass": NullPool} for db in dbs)
    assert sorted(op.client for op in operatons) == ["pilot-local", "qsr-local"]
    assert all(db.disposed for db in dbs) and all(op.http.is_closed for op in operatons)


def test_a_probe_that_breaks_unexpectedly_turns_only_that_client_red(world: World) -> None:
    world.engines["pilot-local"].breaks = True
    found = world.overview()
    pilot, qsr = found["pilot-local"], found["qsr-local"]
    assert pilot["health"]["operaton"] == {"up": False, "ms": None}
    assert pilot["cases_in_progress"] is None
    assert pilot["health"]["db"]["up"] is True
    assert qsr["health"]["operaton"]["up"] is True
    assert qsr["cases_in_progress"] == [{"flow": "qsr_launch", "count": 2}]


def test_matrix_retail_shows_5_1_1_its_cases_per_flow_and_its_release(world: World) -> None:
    pilot = world.overview()["pilot-local"]
    assert pilot["id"] == str(world.rows["pilot-local"].id)
    assert pilot["status"] == "ready"
    assert pilot["users"] == {"active": 5, "invited": 1, "deactivated": 1}
    assert pilot["cases_in_progress"] == [
        {"flow": "store_launch", "count": 4},
        {"flow": "refit", "count": 0},
    ]
    assert pilot["last_release"] == {"version": 3, "published_at": "2026-11-02T09:30:00Z"}
    health = pilot["health"]
    assert health["operaton"]["up"] is True and health["operaton"]["ms"] >= 0
    assert health["db"]["up"] is True and health["db"]["ms"] >= 0
    assert sorted(world.engines["pilot-local"].calls) == [
        "/engine-rest/process-instance/count?processDefinitionKey=zm_pilot__refit",
        "/engine-rest/process-instance/count?processDefinitionKey=zm_pilot__store_launch",
        "/health?",
    ]


def test_a_client_without_a_release_has_no_cases_yet(world: World) -> None:
    world.vaults["qsr-local"].release = None
    qsr = world.overview()["qsr-local"]
    assert qsr["last_release"] is None
    assert qsr["cases_in_progress"] == []
    assert world.engines["qsr-local"].calls == ["/health?"]


def test_one_engine_down_turns_only_that_client_red(world: World) -> None:
    world.engines["pilot-local"].up = False
    found = world.overview()
    pilot, qsr = found["pilot-local"], found["qsr-local"]
    assert pilot["health"]["operaton"] == {"up": False, "ms": None}
    assert pilot["cases_in_progress"] is None
    assert pilot["health"]["db"]["up"] is True  # the vault still answers
    assert pilot["users"] == {"active": 5, "invited": 1, "deactivated": 1}
    assert qsr["health"]["operaton"]["up"] is True
    assert qsr["cases_in_progress"] == [{"flow": "qsr_launch", "count": 2}]


def test_an_engine_that_hangs_is_red_after_the_probe_timeout(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clients, "PROBE_TIMEOUT", 0.05)
    world.engines["pilot-local"].hang = True
    found = world.overview()
    assert found["pilot-local"]["health"]["operaton"] == {"up": False, "ms": None}
    assert found["qsr-local"]["health"]["operaton"]["up"] is True


def test_a_vault_down_turns_only_its_db_red(world: World) -> None:
    world.vaults["pilot-local"].down = True
    pilot = world.overview()["pilot-local"]
    assert pilot["health"]["db"] == {"up": False, "ms": None}
    assert pilot["health"]["operaton"]["up"] is True
    assert pilot["users"] is None
    assert pilot["last_release"] is None
    assert pilot["cases_in_progress"] is None  # its flows are in the vault


@pytest.mark.parametrize(
    "change",
    [{"secrets": b"another key's"}, {"secrets": None}, {"operaton_url": None}],
    ids=["sealed-with-another-key", "no-secrets", "no-operaton-url"],
)
def test_a_client_whose_secrets_dont_open_is_red_and_the_rest_answer(
    world: World, change: dict[str, Any]
) -> None:
    """Sealed with another SECRETS_ENCRYPTION_KEY, or ready without its secrets or Operaton URL:
    neither its vault nor its engine is reachable."""
    world.platform.clients[0] = replace(world.rows["pilot-local"], **change)
    found = world.overview()
    assert found["pilot-local"]["health"] == {
        "operaton": {"up": False, "ms": None},
        "db": {"up": False, "ms": None},
    }
    assert found["pilot-local"]["users"] is None
    assert found["qsr-local"]["health"]["operaton"]["up"] is True


def test_a_client_not_ready_shows_its_provisioning_status_unprobed(world: World) -> None:
    new = world.overview()["new-local"]
    assert new == {
        "id": str(world.rows["new-local"].id),
        "slug": "new-local",
        "status": "provisioning",
        "users": None,
        "cases_in_progress": None,
        "last_release": None,
        "health": None,
    }


def test_the_answer_is_kept_60_s_so_a_down_engine_is_red_within_60_s(world: World) -> None:
    assert world.overview()["pilot-local"]["health"]["operaton"]["up"] is True
    world.engines["pilot-local"].up = False
    world.now[0] += 59.9
    assert world.overview()["pilot-local"]["health"]["operaton"]["up"] is True  # kept
    assert world.vaults["pilot-local"].queries == 1
    world.now[0] += 0.1
    assert world.overview()["pilot-local"]["health"]["operaton"]["up"] is False
    assert world.vaults["pilot-local"].queries == 2


FORBIDDEN = re.compile(r"name|mail|phone|value|field|form|answer|workflow", re.IGNORECASE)


def property_names(schema: Any) -> set[str]:
    """Every property name a JSON schema allows, at any depth."""
    if isinstance(schema, dict):
        own = set(schema.get("properties", {}))
        return own | {k for v in schema.values() for k in property_names(v)}
    if isinstance(schema, list):
        return {k for v in schema for k in property_names(v)}
    return set()


def json_keys(doc: Any) -> set[str]:
    if isinstance(doc, dict):
        return set(doc) | {k for v in doc.values() for k in json_keys(v)}
    if isinstance(doc, list):
        return {k for v in doc for k in json_keys(v)}
    return set()


def test_the_response_has_no_names_emails_or_form_values(world: World) -> None:
    names = property_names(TypeAdapter(list[clients.ClientOverview]).json_schema())
    assert not {n for n in names if FORBIDDEN.search(n)}, names
    assert json_keys(list(world.overview().values())) <= names


# --- integration: the pilot vault and Operaton of the local stack ---------------------------


@pytest.mark.integration
def test_the_pilot_vault_and_engine_answer_and_a_dead_engine_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", PLATFORM_URL)
    cfg = Config(str(INI))
    cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
    command.upgrade(cfg, "head")

    async def setup() -> dict[str, tuple[uuid.UUID, str]]:
        # `down` is pilot's vault with an engine on the discard port, where nothing listens
        return {name: await register(name, "pilot") for name in ("pilot", "down")}

    made = asyncio.run(setup())
    try:
        with TestClient(create_app()) as api:
            response = api.get(URL, headers=bearer(token("owner", client=None)))
    finally:
        ids = [client_id for client_id, _ in made.values()]
        asyncio.run(platform_sql("delete from platform.clients where id = any(:ids)", ids=ids))
    assert response.status_code == 200, response.text
    found = {c["id"]: c for c in response.json()}
    pilot, down = (found[str(made[name][0])] for name in ("pilot", "down"))

    counts = dict(vault_sql("pilot", "select status, count(*) from app.users group by status"))
    users = {s: counts.get(s, 0) for s in ("active", "invited", "deactivated")}
    assert pilot["users"] == users
    assert pilot["health"]["operaton"]["up"] is True
    assert pilot["health"]["db"]["up"] is True
    active = vault_sql("pilot", "select version, process_keys from app.releases where active")
    if not active:
        assert pilot["last_release"] is None
        assert pilot["cases_in_progress"] == []
    else:
        version, process_keys = active[0]
        assert pilot["last_release"]["version"] == version
        with engine_client("pilot") as op:
            running = {  # flow → {"count": n}
                flow: op.get("process-instance/count", params={"processDefinitionKey": key}).json()
                for flow, key in process_keys.items()
            }
        assert pilot["cases_in_progress"] == [{"flow": f, **n} for f, n in running.items()]

    assert down["health"]["operaton"] == {"up": False, "ms": None}
    assert down["health"]["db"]["up"] is True  # the same vault, still answering
    assert down["users"] == users
    assert down["cases_in_progress"] is None
