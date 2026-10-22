"""OperatonClient (#21) against the REST shapes verified at Operaton v2.1.5, mocked with respx."""

import asyncio
import json
import logging

import httpx
import pytest
import respx

from zm_api.errors import NotFound
from zm_api.operaton import OperatonClient, OperatonError, variables

BASE = "http://operaton.test:8080/engine-rest"
run = asyncio.run


@pytest.fixture
def op() -> OperatonClient:
    return OperatonClient(BASE, auth=("zm-service", "pw"), client="pilot-local")


def sent(route: respx.Route) -> httpx.Request:
    return route.calls.last.request


def body(route: respx.Route) -> dict:
    return json.loads(sent(route).content)


@respx.mock
def test_start_by_key_sends_typed_variables_and_business_key(op: OperatonClient) -> None:
    route = respx.post(f"{BASE}/process-definition/key/zm_pilot__store_launch/start").respond(
        200, json={"id": "pi-1", "businessKey": "MX-0001"}
    )
    out = run(
        op.start_by_key(
            "zm_pilot__store_launch", business_key="MX-0001", values={"initiator": "u1"}
        )
    )
    assert out["id"] == "pi-1"
    assert body(route) == {
        "businessKey": "MX-0001",
        "variables": {"initiator": {"value": "u1", "type": "String"}},
    }
    assert sent(route).headers["accept"] == "application/json"


def test_variables_are_typed_and_objects_refused() -> None:
    assert variables({"s": "x", "b": True, "i": 3, "f": 2.5, "n": None}) == {
        "s": {"value": "x", "type": "String"},
        "b": {"value": True, "type": "Boolean"},
        "i": {"value": 3, "type": "Long"},
        "f": {"value": 2.5, "type": "Double"},
        "n": {"value": None, "type": "Null"},
    }
    with pytest.raises(TypeError, match="D5"):
        variables({"doc": {"key": "k"}})


@respx.mock
def test_list_tasks_posts_the_query_with_paging(op: OperatonClient) -> None:
    route = respx.post(f"{BASE}/task").respond(200, json=[{"id": "t1"}])
    query = {"candidateGroups": ["legal__L1"], "processInstanceBusinessKey": "MX-0001"}
    assert run(op.list_tasks(query, max_results=50)) == [{"id": "t1"}]
    assert sent(route).url.params["firstResult"] == "0"
    assert sent(route).url.params["maxResults"] == "50"
    assert body(route) == query


@respx.mock
def test_task_claim_complete_and_history(op: OperatonClient) -> None:
    respx.get(f"{BASE}/task/t1").respond(200, json={"id": "t1", "formKey": "zm:legal_dd_checklist"})
    claim = respx.post(f"{BASE}/task/t1/claim").respond(204)
    complete = respx.post(f"{BASE}/task/t1/complete").respond(204)
    history = respx.get(f"{BASE}/history/task", params={"taskId": "t1"}).respond(
        200, json=[{"id": "t1"}]
    )

    async def flow():
        task = await op.get_task("t1")
        await op.claim("t1", "u1")
        await op.complete("t1", {"legal_dd_checklist_outcome": "done"})
        return task, await op.history_task("t1")

    task, hist = run(flow())
    assert task["formKey"] == "zm:legal_dd_checklist"
    assert body(claim) == {"userId": "u1"}
    assert body(complete) == {
        "variables": {"legal_dd_checklist_outcome": {"value": "done", "type": "String"}}
    }
    assert hist == [{"id": "t1"}] and history.called


@respx.mock
def test_identity_calls(op: OperatonClient) -> None:
    group = respx.post(f"{BASE}/group/create").respond(204)
    user = respx.post(f"{BASE}/user/create").respond(204)
    add = respx.put(f"{BASE}/group/legal__L2/members/0b4c").respond(204)
    remove = respx.delete(f"{BASE}/group/legal__L2/members/0b4c").respond(204)

    async def flow():
        await op.create_group("legal__L2", "Legal L2")
        await op.create_user(
            "0b4c", first_name="Priya", last_name="Rao", email="p@example.com", password="x" * 32
        )
        await op.add_member("legal__L2", "0b4c")
        await op.remove_member("legal__L2", "0b4c")

    run(flow())
    assert body(group) == {"id": "legal__L2", "name": "Legal L2", "type": "zm"}
    assert body(user) == {
        "profile": {
            "id": "0b4c",
            "firstName": "Priya",
            "lastName": "Rao",
            "email": "p@example.com",
        },
        "credentials": {"password": "x" * 32},
    }
    assert add.called and remove.called


@respx.mock
def test_ids_are_escaped_in_paths(op: OperatonClient) -> None:
    route = respx.get(url__regex=r".*/task/a%2Fb$").respond(200, json={})
    run(op.get_task("a/b"))
    assert route.called


@respx.mock
def test_deploy_sends_one_named_part_per_resource(op: OperatonClient) -> None:
    route = respx.post(f"{BASE}/deployment/create").respond(
        200, json={"id": "d1", "deployedProcessDefinitions": {"p:1": {"key": "p"}}}
    )
    out = run(
        op.deploy("zm_pilot", {"main.bpmn": b"<definitions/>", "legal.bpmn": b"<definitions/>"})
    )
    assert out["id"] == "d1"
    content = sent(route).content.decode()
    for part in (
        "deployment-name",
        "deployment-source",
        "enable-duplicate-filtering",
        "deploy-changed-only",
    ):
        assert f'name="{part}"' in content
    # every resource is redeployed when one changed (deployment-bound call activities, #23)
    assert 'name="deploy-changed-only"\r\n\r\nfalse' in content
    assert 'name="main.bpmn"; filename="main.bpmn"' in content
    assert 'name="legal.bpmn"; filename="legal.bpmn"' in content


@respx.mock
def test_deploy_changed_only_can_be_switched_on(op: OperatonClient) -> None:
    route = respx.post(f"{BASE}/deployment/create").respond(200, json={"id": "d1"})
    run(op.deploy("zm_pilot", {"main.bpmn": b"<definitions/>"}, changed_only=True))
    assert 'name="deploy-changed-only"\r\n\r\ntrue' in sent(route).content.decode()


@respx.mock
def test_process_definition_by_id_and_group_rename(op: OperatonClient) -> None:
    by_id = respx.get(url__regex=r".*/process-definition/zm_p%3A1%3Aabc$").respond(
        200, json={"id": "zm_p:1:abc", "deploymentId": "d1"}
    )
    rename = respx.put(f"{BASE}/group/legal__L1").respond(204)
    assert run(op.process_definition_by_id("zm_p:1:abc"))["deploymentId"] == "d1"
    run(op.update_group("legal__L1", "Legal L1 · Counsel"))
    assert by_id.called
    assert body(rename) == {"id": "legal__L1", "name": "Legal L1 · Counsel", "type": "zm"}


@respx.mock
def test_process_definition_by_key(op: OperatonClient) -> None:
    respx.get(f"{BASE}/process-definition/key/p").respond(200, json={"key": "p", "version": 3})
    assert run(op.process_definition("p"))["version"] == 3


@respx.mock
def test_404_is_not_found_and_500_keeps_the_engine_type(op: OperatonClient) -> None:
    respx.get(f"{BASE}/task/gone").respond(
        404, json={"type": "InvalidRequestException", "message": "No task"}
    )
    respx.post(f"{BASE}/task/t1/claim").respond(
        500, json={"type": "TaskAlreadyClaimedException", "message": "claimed by u2", "code": None}
    )

    async def flow():
        with pytest.raises(NotFound):
            await op.get_task("gone")
        with pytest.raises(OperatonError) as info:
            await op.claim("t1", "u1")
        return info.value

    err = run(flow())
    assert (err.status, err.upstream_status, err.upstream_type) == (
        502,
        500,
        "TaskAlreadyClaimedException",
    )
    assert "claimed by" not in (err.detail or "")


@respx.mock
def test_rejected_service_credentials_are_502_with_upstream_401(op: OperatonClient) -> None:
    respx.get(f"{BASE}/process-definition/key/p").respond(401)

    async def flow():
        with pytest.raises(OperatonError) as info:
            await op.process_definition("p")
        return info.value

    err = run(flow())
    assert (err.status, err.upstream_status) == (502, 401)


@respx.mock
def test_unreachable_engine_is_502_without_status(op: OperatonClient) -> None:
    respx.get(f"{BASE}/task/t1").mock(side_effect=httpx.ConnectError("refused"))

    async def flow():
        with pytest.raises(OperatonError) as info:
            await op.get_task("t1")
        return info.value

    err = run(flow())
    assert err.upstream_status is None and err.status == 502


@respx.mock
def test_every_call_logs_op_status_and_latency(
    op: OperatonClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="zm_api.operaton")
    respx.get(f"{BASE}/task/t1").respond(200, json={})
    respx.get(f"{BASE}/task/t2").respond(
        500, json={"type": "ProcessEngineException", "message": "db down"}
    )

    async def flow():
        await op.get_task("t1")
        with pytest.raises(OperatonError):
            await op.get_task("t2")

    run(flow())
    records = [r for r in caplog.records if r.name == "zm_api.operaton"]
    assert [(r.op, r.status, r.client, r.levelname) for r in records] == [
        ("get_task", 200, "pilot-local", "INFO"),
        ("get_task", 500, "pilot-local", "WARNING"),
    ]
    assert all(isinstance(r.ms, float) for r in records)
    assert records[1].error == "ProcessEngineException: db down"


# Bodies as operaton/operaton:2.1.5 answered them for ids that don't exist (probed 2026-10-20).
CLAIM_UNKNOWN = {
    "type": "NullValueException",
    "message": "Cannot find task with id nope-123: task is null",
    "code": 0,
}
COMPLETE_UNKNOWN = {
    "type": "RestException",
    "message": "Cannot complete task nope-123: Cannot find task with id nope-123: task is null",
    "code": 0,
}
MEMBER_UNKNOWN = {
    "type": "NullValueException",
    "message": "No user found with id 'nouser'.: user is null",
    "code": 0,
}
BAD_VALUE = {
    "type": "InvalidRequestException",
    "message": "Cannot convert value 'abc' of type 'Integer' to java type java.lang.Integer",
    "code": None,
}


@respx.mock
def test_unknown_task_on_claim_and_complete_is_404_not_502(op: OperatonClient) -> None:
    respx.post(f"{BASE}/task/nope-123/claim").respond(500, json=CLAIM_UNKNOWN)
    respx.post(f"{BASE}/task/nope-123/complete").respond(500, json=COMPLETE_UNKNOWN)
    respx.put(f"{BASE}/group/legal__L1/members/nouser").respond(500, json=MEMBER_UNKNOWN)

    async def flow():
        with pytest.raises(NotFound):
            await op.claim("nope-123", "u1")
        with pytest.raises(NotFound):
            await op.complete("nope-123", {})
        with pytest.raises(NotFound):
            await op.add_member("legal__L1", "nouser")

    run(flow())


@respx.mock
def test_rejected_values_are_400_keeping_the_engine_type(op: OperatonClient) -> None:
    respx.post(f"{BASE}/process-definition/key/p/start").respond(400, json=BAD_VALUE)

    async def flow():
        with pytest.raises(OperatonError) as info:
            await op.start_by_key("p")
        return info.value

    err = run(flow())
    assert (err.status, err.upstream_status, err.upstream_type) == (
        400,
        400,
        "InvalidRequestException",
    )
    assert "Integer" not in (err.detail or "")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json=["not", "an", "object"]),
        httpx.Response(500, text="plain text"),
        httpx.Response(200, text="<html>not json</html>"),
    ],
    ids=["list-error-body", "text-error-body", "non-json-success"],
)
@respx.mock
def test_odd_answers_are_502_not_crashes(op: OperatonClient, answer: httpx.Response) -> None:
    respx.get(f"{BASE}/task/t1").mock(return_value=answer)

    async def flow():
        with pytest.raises(OperatonError) as info:
            await op.get_task("t1")
        return info.value

    assert run(flow()).status == 502


@respx.mock
def test_empty_success_is_none(op: OperatonClient) -> None:
    respx.post(f"{BASE}/task/t1/claim").respond(204)
    assert run(op.claim("t1", "u1")) is None
