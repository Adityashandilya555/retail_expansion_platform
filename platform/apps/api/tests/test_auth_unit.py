"""Sign-in pieces without a database (#22): the workspace-code rate limit and the Operaton mirror.

The mirror cases follow what Operaton 2.1.5 (our image) answered when probed: a missing profile or
group is 404, creating either twice is 400, and adding a membership twice is a 500 persistence
error, so `mirror` reads before it writes.
"""

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import pytest
import respx

from zm_api.auth import RateLimit
from zm_api.cli import main as cli
from zm_api.operaton import OperatonClient
from zm_api.users import mirror

BASE = "http://operaton.test:8080/engine-rest"
UID = uuid.UUID("0b4c6f2e-1111-4a2b-9c3d-000000000001")
GONE = {"type": "UserNotFoundException", "message": f"User with id {UID} does not exist"}
NO_GROUP = {"type": "InvalidRequestException", "message": "Group with id legal__L1 does not exist"}


def test_users_seed_reads_only_valid_files_under_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    # refused before any database is touched (no PLATFORM_DATABASE_URL is set here)
    assert cli(["users", "seed", "--client", "pilot-local", "/etc/passwd"]) == 2
    assert cli(["users", "seed", "--client", "pilot-local", "../users.json"]) == 2
    assert "not a file inside" in capsys.readouterr().err
    (tmp_path / "bad.json").write_text(json.dumps({"users": [{"email": "x@y.z", "level": 0}]}))
    assert cli(["users", "seed", "--client", "pilot-local", "bad.json"]) == 2
    assert "level" in capsys.readouterr().err


def test_rate_limit_allows_ten_a_minute_per_key() -> None:
    now = [0.0]
    limit = RateLimit(10, 60.0, clock=lambda: now[0])
    assert all(limit.allow("1.2.3.4") for _ in range(10))
    assert not limit.allow("1.2.3.4")
    assert limit.allow("5.6.7.8")  # another address has its own budget
    now[0] = 59.9
    assert not limit.allow("1.2.3.4")
    now[0] = 60.1
    assert limit.allow("1.2.3.4")


@pytest.fixture
def op() -> OperatonClient:
    return OperatonClient(BASE, auth=("zm-service", "pw"), client="pilot-local")


def body(route: respx.Route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_a_new_user_is_created_with_an_unusable_password_and_joins_its_group(
    op: OperatonClient,
) -> None:
    respx.get(f"{BASE}/user/{UID}/profile").respond(404, json=GONE)
    create = respx.post(f"{BASE}/user/create").respond(204)
    respx.get(f"{BASE}/group", params={"member": str(UID)}).respond(200, json=[])
    respx.get(f"{BASE}/group/legal__L1").respond(404, json=NO_GROUP)
    group = respx.post(f"{BASE}/group/create").respond(204)
    add = respx.put(f"{BASE}/group/legal__L1/members/{UID}").respond(204)

    asyncio.run(
        mirror(op, UID, name="Legal Executive", email="l1@pilot.demo", groups={"legal__L1"})
    )

    sent = body(create)
    assert sent["profile"] == {
        "id": str(UID),
        "firstName": "Legal",
        "lastName": "Executive",
        "email": "l1@pilot.demo",
    }
    assert len(sent["credentials"]["password"]) == 32
    assert body(group) == {"id": "legal__L1", "name": "legal L1", "type": "zm"}
    assert add.called


@respx.mock
def test_an_existing_user_gets_only_the_missing_memberships_and_loses_stale_ones(
    op: OperatonClient,
) -> None:
    respx.get(f"{BASE}/user/{UID}/profile").respond(200, json={"id": str(UID)})
    create = respx.post(f"{BASE}/user/create").respond(204)
    current = [{"id": "legal__L1"}, {"id": "bd__L1"}, {"id": "operaton-admin"}]
    respx.get(f"{BASE}/group", params={"member": str(UID)}).respond(200, json=current)
    respx.get(f"{BASE}/group/legal__L2").respond(200, json={"id": "legal__L2"})
    add_l2 = respx.put(f"{BASE}/group/legal__L2/members/{UID}").respond(204)
    add_l1 = respx.put(f"{BASE}/group/legal__L1/members/{UID}").respond(500)
    drop_bd = respx.delete(f"{BASE}/group/bd__L1/members/{UID}").respond(204)
    drop_admin = respx.delete(f"{BASE}/group/operaton-admin/members/{UID}").respond(204)

    groups = {"legal__L1", "legal__L2"}
    asyncio.run(mirror(op, UID, name="Legal Supervisor", email="l2@pilot.demo", groups=groups))

    assert not create.called
    assert add_l2.called and not add_l1.called  # never re-adds: Operaton answers that with a 500
    assert drop_bd.called
    assert not drop_admin.called  # only department-level groups (`<dept>__L<n>`) are ours


@respx.mock
def test_a_deactivated_user_leaves_every_department_group(op: OperatonClient) -> None:
    respx.get(f"{BASE}/user/{UID}/profile").respond(200, json={"id": str(UID)})
    respx.get(f"{BASE}/group", params={"member": str(UID)}).respond(200, json=[{"id": "legal__L1"}])
    drop = respx.delete(f"{BASE}/group/legal__L1/members/{UID}").respond(204)
    asyncio.run(mirror(op, UID, name="Former", email="x@pilot.demo", groups=set()))
    assert drop.called


@respx.mock
def test_operaton_failures_surface(op: OperatonClient) -> None:
    respx.get(f"{BASE}/user/{UID}/profile").mock(side_effect=httpx.ConnectError("down"))
    from zm_api.operaton import OperatonError

    with pytest.raises(OperatonError):
        asyncio.run(mirror(op, UID, name="X", email="x@pilot.demo", groups=set()))
