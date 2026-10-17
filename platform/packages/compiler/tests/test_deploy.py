"""Deploy compiled releases to compose `operaton-pilot` and run cases (#19, `-m integration`).

Uses the service account and port overrides of docker-compose.yml, like infra/operaton/tests.
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from zm_compiler import Release, compile

from .conftest import TWO_STAGE, doc, template

pytestmark = pytest.mark.integration

REST = f"http://localhost:{os.environ.get('OPERATON_PILOT_PORT', '8080')}/engine-rest"
AUTH = (
    os.environ.get("ZM_ADMIN_USER_ID", "zm-service"),
    os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only"),
)


@pytest.fixture
def rest() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=REST, auth=AUTH, timeout=30) as client:
        yield client


@pytest.fixture
def deploy(rest: httpx.Client) -> Iterator[Any]:
    created: list[str] = []

    def _deploy(release: Release, name: str) -> dict[str, Any]:
        files = {n: (n, xml, "application/xml") for n, xml in release.bpmn.items()}
        data = {"deployment-name": name, "deployment-source": "zm-compiler-test"}
        response = rest.post("/deployment/create", data=data, files=files)
        assert response.status_code == 200, response.text
        body = response.json()
        created.append(body["id"])
        return body

    yield _deploy
    for deployment_id in created:
        rest.delete(f"/deployment/{deployment_id}", params={"cascade": "true"})


def definitions(rest: httpx.Client, deployment_id: str) -> list[str]:
    response = rest.get("/process-definition", params={"deploymentId": deployment_id})
    return sorted(d["key"] for d in response.json())


@pytest.mark.parametrize("key", ["matrix-retail", "qsr"])
def test_template_deploys_one_main_process_per_flow_plus_one_per_stage(rest, deploy, key) -> None:
    slug = f"t{uuid.uuid4().hex[:6]}"
    wf = template(key)
    release = compile(wf, slug)
    body = deploy(release, f"{key}-test")
    expected = sorted(
        [release.process_keys[f.key] for f in wf.flows]
        + [f"{release.process_keys[f.key]}__{s.key}" for f in wf.flows for s in f.stages]
    )
    assert definitions(rest, body["id"]) == expected


def run_case(rest: httpx.Client, release: Release, legal_outcome: str) -> str:
    """Start a case, complete BD, decide in Legal; return the main process's case_status."""
    key = release.process_keys["store_launch"]
    business_key = f"T-{uuid.uuid4().hex[:6]}"
    started = rest.post(
        f"/process-definition/key/{key}/start",
        json={
            "businessKey": business_key,
            "variables": {"initiator_groups": {"value": ",bd__L1,", "type": "String"}},
        },
    )
    assert started.status_code == 200, started.text
    main_id = started.json()["id"]
    for task_key, variables in (
        ("bd_task", {}),
        ("legal_review", {"legal_review_outcome": {"value": legal_outcome, "type": "String"}}),
    ):
        tasks = rest.get(
            "/task",
            params={"processInstanceBusinessKey": business_key, "taskDefinitionKey": task_key},
        ).json()
        assert len(tasks) == 1, (task_key, tasks)
        done = rest.post(f"/task/{tasks[0]['id']}/complete", json={"variables": variables})
        assert done.status_code == 204, done.text
    status = rest.get(
        "/history/variable-instance",
        params={"processInstanceId": main_id, "variableName": "case_status"},
    ).json()
    assert len(status) == 1, status
    return status[0]["value"]


def test_case_status_rejected_in_legal_and_completed(rest, deploy) -> None:
    release = compile(doc(TWO_STAGE), f"t{uuid.uuid4().hex[:6]}")
    deploy(release, "case-status-test")
    assert run_case(rest, release, "reject") == "closed:legal"
    assert run_case(rest, release, "approve") == "completed"
