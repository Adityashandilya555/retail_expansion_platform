"""The live-release compatibility gate (S2-09, #43, D14): `zm-worker compat-live` runs before each
API deploy and fails it when a client's active release no longer validates or compiles.

The unit tests feed releases in through `load`; the integration test reads a real row from the
pg-iso vault (`pytest -m integration`).
"""

import copy
import json
import os
import uuid
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.types.json import Jsonb

import zm_worker.__main__ as worker_main
from zm_api import secrets as sealed
from zm_api import versioning
from zm_api.templates import load_templates
from zm_worker import compat_live


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def template(key: str = "matrix-retail") -> dict[str, Any]:
    return copy.deepcopy(next(t.workflow for t in load_templates() if t.key == key))


def client(slug: str, **secrets: str) -> tuple[str, bytes]:
    return slug, sealed.seal(
        {"zm_api_dsn": f"postgresql://zm_api:pw@{slug}-db/postgres", **secrets}
    )


def with_retired_block(doc: dict[str, Any]) -> dict[str, Any]:
    """The release placed a block this build no longer ships."""
    region = next(iter(doc["apps"]["pages"][0]["regions"]))
    doc["apps"]["pages"][0]["regions"][region].append({"block": "retired_block", "props": {}})
    return doc


def test_every_live_release_that_still_validates_and_compiles_passes() -> None:
    releases = {"pilot-db": (3, template()), "qsr-db": (1, template("qsr"))}
    problems = compat_live.check(
        [client("pilot"), client("qsr")], load=lambda dsn: releases[dsn.split("@")[1].split("/")[0]]
    )
    assert problems == []


def test_a_release_this_build_breaks_is_named_with_its_client_and_path() -> None:
    releases = {"pilot-db": (1, template()), "qsr-db": (4, with_retired_block(template("qsr")))}
    problems = compat_live.check(
        [client("pilot"), client("qsr")], load=lambda dsn: releases[dsn.split("@")[1].split("/")[0]]
    )
    assert problems, "the retired block must fail the gate"
    assert {p.source for p in problems} == {"client qsr v4"}
    assert any(
        p.where.startswith("apps.pages[0].regions.") and "retired_block" in p.message
        for p in problems
    )


def test_a_stamped_release_still_validates() -> None:
    """Releases after the first carry S2-25's stamps (`since`, `required_from_version`)."""
    v1 = template()
    v2 = template()
    v2["fields"]["store_size"] = {"type": "number", "label": "Store size", "required": True}
    versioning.stamp([v1["fields"]], v2, 2)
    assert v2["fields"]["store_size"]["since"] == 2
    assert compat_live.check([client("pilot")], load=lambda dsn: (2, v2)) == []


def test_a_vault_that_cant_be_read_fails_the_gate_without_leaking_secrets() -> None:
    def down(dsn: str) -> tuple[int, dict[str, Any]] | None:
        raise psycopg.OperationalError(f'connection to server at "pilot-db" failed (dsn {dsn})')

    (problem,) = compat_live.check([client("pilot")], load=down)
    assert (problem.source, problem.where) == ("client pilot", "vault")
    assert "pw" not in problem.message  # the zm_api password is redacted
    assert "couldn't read the active release" in problem.message


def test_clients_without_a_release_or_a_vault_dsn_are_skipped() -> None:
    no_dsn = ("local-only", sealed.seal({"service_account_password": "x"}))
    assert compat_live.check([client("fresh"), no_dsn, ("bare", None)], load=lambda dsn: None) == []


def test_the_command_exits_non_zero_and_logs_each_problem(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://unused/postgres")
    monkeypatch.setattr(compat_live, "ready_clients", lambda dsn: [client("qsr")])
    monkeypatch.setattr(
        compat_live, "active_release", lambda dsn: (4, with_retired_block(template("qsr")))
    )
    with pytest.raises(SystemExit) as stopped:
        worker_main.main(["compat-live"])
    assert stopped.value.code == 1
    err = capsys.readouterr().err
    assert "FAIL client qsr v4 · apps.pages[0].regions." in err
    assert "compat-live: 1 of 1 live release(s) would break" in err


def test_the_command_passes_quietly_when_every_release_is_fine(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://unused/postgres")
    monkeypatch.setattr(compat_live, "ready_clients", lambda dsn: [client("pilot")])
    monkeypatch.setattr(compat_live, "active_release", lambda dsn: (1, template()))
    worker_main.main(["compat-live"])
    assert "compat-live: 1 live release(s) validate and compile" in capsys.readouterr().out


def test_the_command_needs_the_registry(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PLATFORM_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        worker_main.main(["compat-live"])
    assert "PLATFORM_DATABASE_URL" in capsys.readouterr().err


@pytest.mark.integration
def test_active_release_reads_the_vaults_active_row() -> None:
    """On the migrated pg-iso vault, as the zm_api role; its releases are put back afterwards."""
    port = os.environ.get("PG_ISO_PORT", 5434)
    admin = f"postgresql://postgres:postgres@localhost:{port}/postgres"
    password = os.environ.get("ZM_ISO_API_DB_PASSWORD", "zm-api-iso-local")
    dsn = f"postgresql+asyncpg://zm_api:{password}@localhost:{port}/postgres"
    doc = template("qsr")
    with psycopg.connect(admin, autocommit=True) as c:
        if c.execute("select count(*) from app.releases").fetchone()[0]:
            pytest.skip("pg-iso holds releases of another test run")
        try:
            assert compat_live.active_release(dsn) is None
            for version, active in ((1, False), (2, True)):
                c.execute(
                    "insert into app.releases (version, deployment_id, workflow, forms, ui,"
                    " routing_vars, process_keys, sha256, active)"
                    " values (%s, %s, %s, '{}', '{}', '{}', '{}', 'x', %s)",
                    (version, f"dep-{uuid.uuid4()}", Jsonb({**doc, "v": version}), active),
                )
            version, workflow = compat_live.active_release(dsn) or (0, {})
            assert version == 2 and json.dumps(workflow["v"]) == "2"
        finally:
            c.execute("delete from app.releases")
