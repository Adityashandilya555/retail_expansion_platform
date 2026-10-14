"""S1-02 (#11): our Operaton image, checked against compose `operaton-pilot` (scripts/dev-up.sh).

Host ports and the service account follow docker-compose.yml, including its overrides.
Each test cleans up after itself, so it also passes on a kept data volume.
"""

import os
import uuid

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.integration

BASE = f"http://localhost:{os.environ.get('OPERATON_PILOT_PORT', '8080')}"
REST = f"{BASE}/engine-rest"
SERVICE_ACCOUNT = (
    os.environ.get("ZM_ADMIN_USER_ID", "zm-service"),
    os.environ.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only"),
)
SUPABASE_DB = 54322
PG_ISO = int(os.environ.get("PG_ISO_PORT", "5434"))
GROUP = {"id": "legal__L2", "name": "Legal L2", "type": "WORKFLOW"}

BPMN_WITHOUT_TTL = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="zm-s1-02">
  <process id="zm_s1_02_no_ttl" isExecutable="true">
    <startEvent id="start"/>
    <sequenceFlow id="flow" sourceRef="start" targetRef="end"/>
    <endEvent id="end"/>
  </process>
</definitions>
"""


@pytest.fixture
def rest():
    with httpx.Client(base_url=REST, auth=SERVICE_ACCOUNT, timeout=10) as client:
        yield client


@pytest.fixture
def legal_l2(rest):
    rest.delete(f"/group/{GROUP['id']}")
    response = rest.post("/group/create", json=GROUP)
    yield response
    rest.delete(f"/group/{GROUP['id']}")


def test_group_id_with_level_suffix_is_accepted(legal_l2):
    assert legal_l2.status_code == 204, legal_l2.text


def test_uuid_user_is_created_and_joins_the_group(rest, legal_l2):
    user_id = str(uuid.uuid4())
    created = rest.post(
        "/user/create",
        json={
            "profile": {
                "id": user_id,
                "firstName": "S1",
                "lastName": "Two",
                "email": "s1@example.com",
            },
            "credentials": {"password": "not-a-real-Password-1"},
        },
    )
    try:
        assert created.status_code == 204, created.text
        joined = rest.put(f"/group/{GROUP['id']}/members/{user_id}")
        assert joined.status_code == 204, joined.text
    finally:
        rest.delete(f"/user/{user_id}")


def test_id_pattern_is_still_enforced(rest):
    response = rest.post("/group/create", json={"id": "bad id!", "name": "Bad", "type": "WORKFLOW"})
    # Operaton 2.1.5 maps the whitelist's ProcessEngineException to 500, not 400 (EnsureUtil.java).
    assert response.status_code == 500, response.text
    assert "not a valid resource identifier" in response.json()["message"]


def test_rest_needs_credentials_but_health_does_not():
    assert httpx.get(f"{REST}/task", timeout=10).status_code == 401
    assert httpx.get(f"{BASE}/health", timeout=10).status_code == 200


def test_webapps_are_off():
    assert httpx.get(f"{BASE}/operaton/app/tasklist/", timeout=10).status_code == 404


def test_bpmn_without_history_ttl_deploys(rest):
    response = rest.post(
        "/deployment/create",
        data={"deployment-name": "zm-s1-02-no-ttl"},
        files={"process.bpmn": ("process.bpmn", BPMN_WITHOUT_TTL, "text/xml")},
    )
    assert response.status_code == 200, response.text
    rest.delete(f"/deployment/{response.json()['id']}", params={"cascade": "true"})


@pytest.mark.parametrize("db_port", [SUPABASE_DB, PG_ISO])
def test_engine_tables_live_only_in_operaton_schema(db_port):
    with psycopg.connect(
        f"postgresql://postgres:postgres@localhost:{db_port}/postgres", connect_timeout=5
    ) as conn:
        rows = conn.execute(
            "select table_schema, count(*) from information_schema.tables"
            " where table_name like 'act\\_%' group by table_schema"
        ).fetchall()
    assert set(dict(rows)) == {"operaton"}, rows
