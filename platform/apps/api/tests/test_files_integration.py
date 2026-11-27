"""Files (#51) against the local stack: `pytest -m integration` after scripts/dev-up.sh, with
LOCAL_STORAGE_SERVICE_KEY set (`SERVICE_ROLE_KEY` from `supabase --workdir infra status -o env`).

Real signed uploads to the Supabase CLI's Storage, confirm, download and the submit check. pilot
runs Matrix Retail where the DD checklist has four document slots: `dd_report` (Fire NOC's rule:
PDF, 25 MB, versions kept), `dd_annexure` (versions kept), `dd_photo` (PNG/JPG, latest only) and
`dd_partner_report` in a section hidden below Legal L2; the start form has `site_plan`. One case
is started with a start upload and driven to the DD checklist; the tests follow it in order, and
the last one submits it. Another client's file id is the isolation suite's check (#31).
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from zm_api import secrets
from zm_api.main import create_app
from zm_api.publish import publish
from zm_api.releases import cache_clear
from zm_api.storage import auth_headers
from zm_api.users import seed_users
from zm_worker.provision.supabase import ensure_bucket

from .conftest import JWT_SECRET
from .test_cases_integration import START, TO_LEGAL_FROM_L1, World, engine_client
from .test_publish import (
    ENGINES,
    INI,
    PLATFORM_URL,
    SERVICE_PASSWORDS,
    ZM_API_PASSWORDS,
    platform_sql,
    vault_sql,
)
from .test_tasks import Stack
from .test_tasks_unit import matrix_retail

pytestmark = pytest.mark.integration

KEY = Fernet.generate_key().decode()
DOMAIN = "files.test"
USERS = {"bd1": ("bd", 1), "bd2": ("bd", 2), "legal1": ("legal", 1), "legal2": ("legal", 2)}
STORAGE_URL = os.environ.get("LOCAL_STORAGE_URL", "http://127.0.0.1:54321")
STORAGE_KEY = os.environ.get("LOCAL_STORAGE_SERVICE_KEY", "")
MB = 1024 * 1024


def pdf(size: int) -> bytes:
    return b"%PDF-1.7\n" + b"0" * (size - 9)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in env_vars().items():
        monkeypatch.setenv(k, v)


def env_vars() -> dict[str, str]:
    return {
        "SECRETS_ENCRYPTION_KEY": KEY,
        "JWT_SECRET": JWT_SECRET,
        "PLATFORM_DATABASE_URL": PLATFORM_URL,
    }


def workflow() -> dict[str, Any]:
    doc = matrix_retail()
    fields, forms = doc["fields"], doc["forms"]

    def slot(label: str, accept: list[str], max_mb: int, keep: bool) -> dict[str, Any]:
        rule = {"accept": accept, "max_mb": max_mb, "keep_versions": keep}
        return {"type": "document", "label": label, "document": rule}

    fields["dd_report"] = slot("DD report", ["pdf"], 25, True)  # Fire NOC's rule
    fields["dd_annexure"] = slot("Annexure", ["pdf"], 5, True)
    fields["dd_photo"] = slot("Site photo", ["png", "jpg"], 5, False)
    fields["dd_partner_report"] = slot("Partner report", ["pdf"], 5, False)
    fields["site_plan"] = slot("Site plan", ["pdf"], 5, False)
    forms["legal_dd_checklist"]["sections"][0]["rows"] += [
        [{"field": "dd_annexure"}],
        [{"field": "dd_photo"}],
    ]
    forms["legal_dd_checklist"]["sections"].append(
        {
            "key": "partner",
            "title": "Partner review",
            "hidden_below_level": {"department": "legal", "level": 2},
            "rows": [[{"field": "dd_partner_report"}]],
        }
    )
    forms["start"]["sections"][0]["rows"].append([{"field": "site_plan"}])
    return doc


def clear() -> None:
    vault_sql("pilot", "delete from app.files")
    for table in ("app.cases", "app.case_counters", "app.releases", "app.drafts"):
        vault_sql("pilot", f"delete from {table}")
    vault_sql("pilot", "delete from app.users where email like %s", f"%@{DOMAIN}")


def storage(
    method: str, path: str, headers: dict[str, str] | None = None, **kwargs: Any
) -> httpx.Response:
    """Storage as the service (tests only): see what the API left in the bucket."""
    url = f"{STORAGE_URL}/storage/v1/{path}"
    return httpx.request(
        method, url, headers={**auth_headers(STORAGE_KEY), **(headers or {})}, **kwargs
    )


async def register() -> tuple[uuid.UUID, str]:
    """pilot with the Supabase CLI's Storage sealed, as `provision-local` leaves it."""
    slug = f"pilot-{uuid.uuid4().hex[:8]}"
    dsn = f"postgresql://zm_api:{ZM_API_PASSWORDS['pilot']}@localhost:54322/postgres"
    sealed = secrets.seal(
        {
            "zm_api_dsn": dsn,
            "service_account_password": SERVICE_PASSWORDS["pilot"],
            "storage_url": STORAGE_URL,
            "service_role_key": STORAGE_KEY,
        }
    )
    rows = await platform_sql(
        "insert into platform.clients (slug, name, workspace_code, template_key, status,"
        " operaton_url, bucket, secrets) values (:slug, 'Pilot', :code, 'matrix-retail',"
        " 'ready', :url, 'files', :sealed) returning id",
        slug=slug,
        code=slug.upper(),
        url=ENGINES["pilot"],
        sealed=sealed,
    )
    return rows[0]["id"], slug


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    if not STORAGE_KEY:
        pytest.fail("set LOCAL_STORAGE_SERVICE_KEY: SERVICE_ROLE_KEY of `supabase status -o env`")
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear()
        cache_clear()
        with httpx.Client(timeout=15) as http:
            ensure_bucket(http, STORAGE_URL, STORAGE_KEY)

        async def setup() -> tuple[uuid.UUID, str, dict[str, uuid.UUID]]:
            client_id, slug = await register()
            vault_sql(
                "pilot",
                "insert into app.drafts (id, workflow, template_key) values (1, %s::jsonb, 'x')",
                json.dumps(workflow()),
            )
            async with Stack() as stack:
                ctx = await stack.ctx(slug)
                await publish(ctx, stack.registry.engine(), None)
                seeded = await seed_users(
                    ctx,
                    [
                        {"email": f"{u}@{DOMAIN}", "name": u.title(), "department": d, "level": n}
                        for u, (d, n) in USERS.items()
                    ],
                )
            return client_id, slug, {s.email.split("@")[0]: s.id for s in seeded}

        client_id, slug, users = asyncio.run(setup())
        try:
            with TestClient(create_app()) as http:
                yield World(http, slug, users, {})
        finally:
            keys = [k for (k,) in vault_sql("pilot", "select key from app.files")]
            if keys:
                storage("DELETE", "object/files", json={"prefixes": keys})
            asyncio.run(platform_sql("delete from platform.clients where id = :i", i=client_id))
            clear()
            cache_clear()
            with engine_client("pilot") as op:
                for d in op.get("deployment", params={"name": f"{slug}-release"}).json():
                    op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def begin(w: World, user: str, name: str, size: int, **slot: str) -> httpx.Response:
    body = {**slot, "name": name, "size_bytes": size}
    return w.http.post("/app/files/upload", json=body, headers=w.auth(user))


def upload(w: World, user: str, name: str, data: bytes, **slot: str) -> tuple[str, httpx.Response]:
    """Upload as the browser does: the API's signed URL, a PUT to Storage, then confirm."""
    started = begin(w, user, name, len(data), **slot)
    assert started.status_code == 201, started.text
    sent = httpx.put(
        started.json()["upload_url"], content=data, headers={"Content-Type": "application/pdf"}
    )
    assert sent.status_code == 200, sent.text
    file_id = started.json()["file_id"]
    return file_id, w.http.post(f"/app/files/{file_id}/confirm", headers=w.auth(user))


def task_id(w: World, user: str, code: str, task: str = "legal_dd_checklist") -> str:
    found = [
        t
        for t in w.get(user, "/app/tasks", scope="mine", case=code)
        if t["form_key"] == f"zm:{task}"
    ]
    assert found, (user, task)
    return found[0]["id"]


@pytest.fixture(scope="module")
def case(world: World) -> dict[str, Any]:
    """MX-0001, started by BD L1 with a site plan uploaded on the start form, at Legal's DD."""
    file_id, confirmed = upload(
        world, "bd1", "plan.pdf", pdf(2048), flow="store_launch", field="site_plan"
    )
    assert confirmed.status_code == 200, confirmed.text
    values = {**START, "site_plan": confirmed.json()}
    started = world.http.post(
        "/app/cases", json={"flow": "store_launch", "values": values}, headers=world.auth("bd1")
    )
    assert started.status_code == 201, started.text
    code = started.json()["code"]
    for user, task, body in TO_LEGAL_FROM_L1:
        world.do(user, task, code, **body)
    return {
        "code": code,
        "id": started.json()["id"],
        "plan": file_id,
        "task": task_id(world, "legal1", code),
    }


def test_a_start_upload_is_keyed_by_user_and_claimed_by_its_case(world: World, case: dict) -> None:
    ((case_id, key, version),) = vault_sql(
        "pilot", "select case_id, key, version from app.files where id = %s", case["plan"]
    )
    assert str(case_id) == case["id"] and version == 1
    assert key.startswith(f"start/store_launch/{world.users['bd1']}/site_plan/")
    listed = world.get("bd2", f"/app/cases/{case['code']}/files", field="site_plan")
    assert [(f["field"], f["version"], f["name"]) for f in listed] == [("site_plan", 1, "plan.pdf")]


def test_a_renamed_exe_is_refused_and_deleted_and_a_30_mb_pdf_never_uploads(
    world: World, case: dict
) -> None:
    file_id, confirmed = upload(
        world,
        "legal1",
        "noc.pdf",
        b"MZ\x90\x00" + b"0" * 4096,
        task_id=case["task"],
        field="dd_report",
    )
    assert confirmed.status_code == 422
    assert confirmed.json()["detail"] == "This isn't a real PDF file"
    ((key, status),) = vault_sql(
        "pilot", "select key, status from app.files where id = %s", file_id
    )
    assert status == "rejected"
    assert storage("GET", f"object/authenticated/files/{key}").status_code in {400, 404}

    big = begin(world, "legal1", "noc.pdf", 30 * MB, task_id=case["task"], field="dd_report")
    assert big.status_code == 422 and big.json()["detail"] == "The file is larger than 25 MB"
    wrong = begin(world, "legal1", "noc.exe", 1024, task_id=case["task"], field="dd_report")
    assert wrong.status_code == 422 and wrong.json()["detail"] == "Only PDF files go here"


def test_kept_versions_list_newest_first_each_downloadable_else_only_the_latest(
    world: World, case: dict
) -> None:
    sent = {}
    for n in (1, 2):
        data = pdf(1024 * n)
        _, confirmed = upload(
            world, "legal1", f"annex-{n}.pdf", data, task_id=case["task"], field="dd_annexure"
        )
        assert confirmed.status_code == 200, confirmed.text
        sent[f"annex-{n}.pdf"] = data
        _, photo = upload(
            world, "legal1", f"photo-{n}.png", PNG, task_id=case["task"], field="dd_photo"
        )
        assert photo.status_code == 200, photo.text
    path = f"/app/cases/{case['code']}/files"
    annex = world.get("legal2", path, field="dd_annexure")
    assert [(f["version"], f["name"]) for f in annex] == [(2, "annex-2.pdf"), (1, "annex-1.pdf")]
    for f in annex:
        signed = world.get("legal2", f"/app/files/{f['id']}/download", attachment="true")
        got = httpx.get(signed["url"])
        assert got.status_code == 200 and got.content == sent[f["name"]]
        assert f["name"] in got.headers["content-disposition"]
    photos = world.get("legal2", path, field="dd_photo")
    assert [(f["version"], f["name"]) for f in photos] == [(2, "photo-2.png")]


def test_a_document_key_of_another_field_or_never_confirmed_is_refused(
    world: World, case: dict
) -> None:
    _, photo = upload(world, "legal1", "p.png", PNG, task_id=case["task"], field="dd_photo")
    pending = begin(world, "legal1", "dd.pdf", 2048, task_id=case["task"], field="dd_report")
    never = {"key": pending.json()["key"], "sha256": "0" * 64, "name": "dd.pdf"}
    plan = world.get("bd1", f"/app/cases/{case['code']}/files", field="site_plan")[0]
    ((plan_key,),) = vault_sql("pilot", "select key from app.files where id = %s", plan["id"])
    start_value = {"key": plan_key, "sha256": "0" * 64, "name": "plan.pdf"}
    for value in (photo.json(), never, start_value):
        headers = {**world.auth("legal1"), "Idempotency-Key": str(uuid.uuid4())}
        sent = world.http.post(
            f"/app/tasks/{case['task']}/submit",
            json={"values": {"dd_report": value}},
            headers=headers,
        )
        assert sent.status_code == 422, sent.text
        assert sent.json()["errors"] == [
            {"path": "/dd_report", "message": "Not a file uploaded to this field"}
        ]


def test_a_slot_hidden_from_the_users_level_issues_no_url(world: World, case: dict) -> None:
    hidden = begin(world, "legal1", "p.pdf", 2048, task_id=case["task"], field="dd_partner_report")
    assert hidden.status_code == 404
    # Legal L2 can see the field: give it a file, as if their own form had taken one
    key = f"cases/{case['code']}/legal_dd_checklist/dd_partner_report/{uuid.uuid4()}-p.pdf"
    data = pdf(2048)
    put = storage(
        "POST", f"object/files/{key}", content=data, headers={"Content-Type": "application/pdf"}
    )
    assert put.status_code == 200, put.text
    ((file_id,),) = vault_sql(
        "pilot",
        "insert into app.files (case_id, flow_key, task_def_key, field, key, name, size_bytes,"
        " sha256, version, status, uploaded_by, confirmed_at) values (%s, 'store_launch',"
        " 'legal_dd_checklist', 'dd_partner_report', %s, 'p.pdf', %s, %s, 1, 'confirmed', %s,"
        " now()) returning id",
        case["id"],
        key,
        len(data),
        "0" * 64,
        world.users["legal2"],
    )
    path = f"/app/cases/{case['code']}/files"
    l1 = world.auth("legal1")
    assert world.http.get(f"/app/files/{file_id}/download", headers=l1).status_code == 404
    assert (
        world.http.get(path, params={"field": "dd_partner_report"}, headers=l1).status_code == 404
    )
    assert "dd_partner_report" not in {f["field"] for f in world.get("legal1", path)}
    assert world.get("legal2", f"/app/files/{file_id}/download")["url"]  # positive control


def test_a_signed_download_url_stops_working_after_60_seconds(world: World, case: dict) -> None:
    newest = world.get("legal1", f"/app/cases/{case['code']}/files", field="dd_annexure")[0]
    signed = world.get("legal1", f"/app/files/{newest['id']}/download")
    assert httpx.get(signed["url"]).status_code == 200
    time.sleep(61)
    assert httpx.get(signed["url"]).status_code != 200


def test_a_pdf_uploads_confirms_and_submits_as_its_value(world: World, case: dict) -> None:
    """Fire NOC's rule: a 2 MB PDF; the stored value is `{key, sha256, name}`, app.files has the
    key, size and sha256 and no URL, and the case lists version 1."""
    data = pdf(2 * MB)
    file_id, confirmed = upload(
        world, "legal1", "Fire NOC.pdf", data, task_id=case["task"], field="dd_report"
    )
    assert confirmed.status_code == 200, confirmed.text
    value = confirmed.json()
    assert set(value) == {"key", "sha256", "name"} and value["name"] == "Fire NOC.pdf"
    assert value["key"].startswith(f"cases/{case['code']}/legal_dd_checklist/dd_report/")
    row = vault_sql("pilot", "select to_jsonb(f) from app.files f where id = %s", file_id)[0][0]
    assert (row["key"], row["size_bytes"], row["status"]) == (value["key"], 2 * MB, "confirmed")
    assert row["sha256"] == value["sha256"] and row["content_type"] == "application/pdf"
    assert not any("http" in str(v) for v in row.values())

    world.do("legal1", "legal_dd_checklist", case["code"], values={"dd_report": value})
    ((stored,),) = vault_sql(
        "pilot",
        "select value from app.case_values where case_id = %s and field = 'dd_report'",
        case["id"],
    )
    assert stored == value
    listed = world.get("legal1", f"/app/cases/{case['code']}/files", field="dd_report")
    assert [(f["version"], f["name"]) for f in listed] == [(1, "Fire NOC.pdf")]
