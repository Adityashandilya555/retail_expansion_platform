"""Preview (#42): a 15-minute read-only token shows the draft in the client shell as one role,
with fixture cases and tasks; nothing reaches Operaton or the client's real cases."""

import copy
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from zm_api import auth, drafts, preview, publish, ui
from zm_api.context import PREVIEW_ROUTES, Preview, Resolver
from zm_api.main import create_app
from zm_api.operaton import OperatonClient
from zm_api.registry import Client
from zm_api.releases import ReleaseRow

from .conftest import JWT_SECRET, bearer, token
from .test_publish_unit import FakePlatform, FakeRegistry, matrix_retail

LEGAL_L1 = Preview("legal", 1, "Legal L1")
AS_LEGAL_L1 = {"as": {"department": "legal", "level": 1}, "label": "Legal L1"}


def with_field() -> dict[str, Any]:
    """The template plus one field on `legal_dd_checklist` (the issue's first criterion)."""
    draft = copy.deepcopy(matrix_retail())
    draft["fields"]["dd_extra"] = {"type": "text", "label": "Extra check"}
    draft["forms"]["legal_dd_checklist"]["sections"][0]["rows"].append([{"field": "dd_extra"}])
    return draft


def preview_token(**claims: Any) -> str:
    return token("preview", **{**AS_LEGAL_L1, **claims})


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The client's draft, as `preview.draft_release` would read and compile it."""
    state: dict[str, Any] = {"draft": with_field()}

    async def draft_release(ctx: Any) -> ReleaseRow:
        return preview.build(ctx.client.slug, state["draft"])

    monkeypatch.setattr(preview, "draft_release", draft_release)
    return state


@pytest.fixture
def api(rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def get_client(_conn: Any, by: str, value: Any) -> Client | None:
        return next((row for row in rows.values() if row.id == value), None)

    monkeypatch.setattr(drafts, "get_client", get_client)
    app = create_app(resolver=Resolver(FakeRegistry(rows)))
    app.state.platform = FakePlatform()
    return TestClient(app)


# --- the token ---------------------------------------------------------------------------------


def test_an_owner_gets_a_15_minute_preview_token_for_a_role(
    api: TestClient, rows: dict[str, Client], state: dict[str, Any]
) -> None:
    url = f"/owner/clients/{rows['pilot-local'].id}/preview-token"
    body = {"as": {"department": "legal", "level": 1}}
    assert api.post(url, json=body).status_code == 401
    assert api.post(url, json=body, headers=bearer(token("client"))).status_code == 403

    owner = bearer(token("owner", client=None))
    out = api.post(url, json=body, headers=owner)
    assert out.status_code == 200, out.text
    assert out.json()["expires_in"] == 900
    claims = jwt.decode(out.json()["access_token"], JWT_SECRET, algorithms=["HS256"])
    assert claims["kind"] == "preview" and claims["client"] == "pilot-local"
    assert claims["as"] == {"department": "legal", "level": 1}
    assert claims["label"] == "Legal L1"
    assert 890 <= claims["exp"] - time.time() <= 900

    for role in ({"department": "nope", "level": 1}, {"department": "legal", "level": 9}):
        assert api.post(url, json={"as": role}, headers=owner).status_code == 422


def test_a_preview_token_without_a_role_is_401(api: TestClient) -> None:
    assert api.get("/app/me", headers=bearer(token("preview"))).status_code == 401
    expired = preview_token(exp=int(time.time()) - 1)
    assert api.get("/app/me", headers=bearer(expired)).status_code == 401


# --- criterion 2: read-only --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/app/tasks/legal_dd_checklist/submit"),
        ("post", "/app/cases"),
        ("put", "/app/me/preferences"),
        ("post", "/app/admin/users"),
        ("post", "/app/admin/change-requests"),
        ("get", "/app/admin/users"),  # a read, but of the client's real people
        ("get", "/app/admin/change-requests"),
        ("get", "/owner/me"),
    ],
)
def test_a_preview_token_cant_write_or_read_real_data(
    api: TestClient, state: dict[str, Any], method: str, path: str
) -> None:
    body = {} if method == "get" else {"json": {}}
    out = api.request(method, path, headers=bearer(preview_token()), **body)
    assert out.status_code == 403, out.text


SIGN_IN = ("/auth/", "/owner/auth/")  # public sign-in namespaces (zm_api.context)


def test_every_route_outside_the_preview_allowlist_refuses_a_preview_token(
    api: TestClient, state: dict[str, Any]
) -> None:
    """Closed by default: every operation that needs a token, other than the GETs in
    `PREVIEW_ROUTES`, is 403 to a preview token, so a route added later (e.g. a GET taking only
    `ClientCtx`) can't answer one by mistake. Walks the app's own OpenAPI paths."""
    paths = api.app.openapi()["paths"]
    assert set(paths) >= PREVIEW_ROUTES  # the allowlist names live routes only
    probed = 0
    for template, operations in paths.items():
        if template.startswith(SIGN_IN):
            continue
        path = re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), template)
        for method in operations:
            if method == "get" and template in PREVIEW_ROUTES:
                continue
            anonymous = api.request(method, path)
            if anonymous.status_code != 401:  # public, e.g. /healthz
                assert not template.startswith(("/app", "/owner")), (method, template)
                continue
            out = api.request(method, path, headers=bearer(preview_token()))
            assert out.status_code == 403, (method, template, out.status_code, out.text)
            probed += 1
    assert probed >= 10  # the walk found the app's routes


def test_a_preview_never_calls_operaton(
    api: TestClient, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def no_call(self: Any, op: str, method: str, path: str, **kwargs: Any) -> Any:
        calls.append(f"{op} {method} {path}")
        raise AssertionError("a preview called Operaton")

    async def no_send(self: Any, request: httpx.Request, **kwargs: Any) -> Any:
        calls.append(str(request.url))
        raise AssertionError("a preview made an outbound request")

    monkeypatch.setattr(OperatonClient, "_call", no_call)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_send)
    who = bearer(preview_token(**{"as": {"department": "legal", "level": 2}}))
    code = api.get("/app/cases", headers=who).json()[0]["code"]
    for path in (
        "/app/me",
        "/app/ui",
        "/app/tasks",
        "/app/tasks?scope=team",
        "/app/tasks/legal_dd_checklist",
        "/app/cases",
        f"/app/cases/{code}",
    ):
        assert api.get(path, headers=who).status_code == 200, path
    assert calls == []


# --- criterion 1: the draft's field shows, and the diff lists it -------------------------------


def test_a_field_added_to_the_draft_shows_in_the_preview_and_the_diff(
    api: TestClient, state: dict[str, Any]
) -> None:
    out = api.get("/app/tasks/legal_dd_checklist", headers=bearer(preview_token()))
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["editable"] is False  # a preview can't submit
    assert body["form"]["schema"]["properties"]["dd_extra"]["title"] == "Extra check"
    assert body["task"]["stage"] == "legal" and body["task"]["case_code"] == body["case"]["code"]

    assert drafts.diff(matrix_retail(), state["draft"]).fields.added == ["dd_extra"]


def test_tasks_are_the_draft_tasks_of_the_role(api: TestClient, state: dict[str, Any]) -> None:
    mine = api.get("/app/tasks", headers=bearer(preview_token())).json()
    assert [t["id"] for t in mine] == ["legal_dd_checklist"]
    assert mine[0]["form_key"] == "zm:legal_dd_checklist" and mine[0]["flow"] == "store_launch"
    # team tasks are for level 2 and above, as live
    team = api.get("/app/tasks?scope=team", headers=bearer(preview_token()))
    assert team.status_code == 403

    l2 = bearer(preview_token(**{"as": {"department": "legal", "level": 2}}))
    team_ids = {t["id"] for t in api.get("/app/tasks?scope=team", headers=l2).json()}
    assert {"legal_dd_checklist", "legal_dd_verdict", "legal_agreement"} <= team_ids
    assert "legal_change_request" not in team_ids  # bd's step in the legal stage
    # another department's task isn't there to open
    assert api.get("/app/tasks/bd_send_legal", headers=l2).status_code == 404
    assert api.get("/app/tasks/legal_dd_checklist", headers=l2).json()["editable"] is False


def test_me_is_the_previewed_role(api: TestClient, state: dict[str, Any]) -> None:
    me = api.get("/app/me", headers=bearer(preview_token())).json()
    assert me["preview"] is True and me["name"] == "Legal L1" and not me["is_client_admin"]
    assert me["client"] == {"slug": "pilot-local", "name": "Pilot-Local"}
    assert [(m["department"], m["level"]) for m in me["memberships"]] == [("legal", 1)]


# --- criterion 3: fixture cases ----------------------------------------------------------------


def test_sites_come_from_fixture_cases_one_per_stage(
    api: TestClient, state: dict[str, Any]
) -> None:
    rows = api.get("/app/cases", headers=bearer(preview_token())).json()
    stages = [s["key"] for s in state["draft"]["flows"][0]["stages"]]
    assert [r["code"] for r in rows] == [f"MX-{n:04d}" for n in range(1, len(stages) + 1)]
    legal = rows[stages.index("legal")]
    states = {s["key"]: s["state"] for s in legal["stages"]}
    assert states["legal"] == "open" and states["bd_loi"] == "done"
    assert states["bd_qualification"] == "done" and states["design"] == "queued"
    assert legal["status"] == "open" and legal["flow"] == "store_launch"
    assert legal["title"] == legal["fields"]["site_name"] != ""
    assert legal["my_tasks"] == 1 and legal["open_tasks"] == 6
    assert api.get("/app/cases?status=closed", headers=bearer(preview_token())).json() == []
    assert api.get("/app/cases?flow=nope", headers=bearer(preview_token())).json() == []

    page = api.get(f"/app/cases/{legal['code']}", headers=bearer(preview_token()))
    assert page.status_code == 200, page.text
    detail = page.json()
    fields = {f["key"]: f["value"] for f in detail["sections"][0]["fields"]}
    assert fields["site_name"] == legal["title"] and fields["city"]
    assert [(t["id"], t["can_act"]) for t in detail["tasks"]] == [("legal_dd_checklist", True)]
    assert [e["kind"] for e in detail["events"]] == ["created"] and detail["history"] == []
    assert api.get("/app/cases/MX-9999", headers=bearer(preview_token())).status_code == 404


def test_flows_sharing_a_code_pattern_get_unique_fixture_codes() -> None:
    release = preview.build("pilot-local", matrix_retail())
    first = release.workflow["flows"][0]
    release.workflow["flows"].append({**copy.deepcopy(first), "key": "second"})
    rows = preview.list_cases(release, LEGAL_L1, None, None)
    codes = [r.code for r in rows]
    assert len(set(codes)) == len(codes) == 2 * len(first["stages"])
    later = next(r for r in rows if r.flow == "second")
    assert preview.get_case(release, LEGAL_L1, later.code).row.flow == "second"
    assert preview.get_case(release, LEGAL_L1, "MX-0001").row.flow == first["key"]


def test_a_fixture_task_shows_the_values_of_its_own_case(
    api: TestClient, state: dict[str, Any]
) -> None:
    """Made-up values are numbered by their case (`City 3` on MX-0003), the task's too."""
    l2 = bearer(preview_token(**{"as": {"department": "legal", "level": 2}}))
    detail = api.get("/app/tasks/legal_dd_verdict", headers=l2).json()
    code = detail["case"]["code"]
    n = int(code.split("-")[1])
    assert n > 1  # legal isn't the first stage, so a case numbered 1 would be someone else's
    texts = {f: v for f, v in detail["show"].items() if isinstance(v, str)}
    assert texts, detail["show"]
    assert all(v.endswith(f" {n}") for v in texts.values()), (code, texts)
    case = api.get(f"/app/cases/{code}", headers=l2).json()
    assert case["fields"]["city"] == f"City {n}"


def test_the_compiled_draft_is_reused_until_it_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    preview._COMPILED.clear()
    compiles: list[str] = []
    real = publish._compile

    def counted(slug: str, draft: dict[str, Any], published: Any = ()) -> Any:
        compiles.append(slug)
        return real(slug, draft, published)

    monkeypatch.setattr(publish, "_compile", counted)
    first = preview.build("pilot-local", matrix_retail())
    first.forms["legal_dd_checklist"]["schema"]["title"] = "changed by a reader"
    again = preview.build("pilot-local", matrix_retail())
    assert compiles == ["pilot-local"]
    assert again.forms["legal_dd_checklist"]["schema"].get("title") != "changed by a reader"
    preview.build("pilot-local", with_field())
    assert len(compiles) == 2


def test_sample_values_follow_the_field_types() -> None:
    assert preview.sample({"type": "number", "label": "Rent"}, 3) == 3
    assert preview.sample({"type": "text", "label": "City"}, 3) == "City 3"
    assert preview.sample({"type": "yes_no", "label": "Ok"}, 3) is True
    options = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    assert preview.sample({"type": "select", "label": "S", "options": options}, 1) == "a"
    assert preview.sample({"type": "multi_select", "label": "S", "options": options}, 1) == ["a"]
    assert preview.sample({"type": "document", "label": "LOI"}, 1) is None


# --- criterion 4: preview equals what publish makes live ---------------------------------------


def test_the_preview_ui_equals_the_live_ui_once_the_draft_is_published(
    api: TestClient, state: dict[str, Any]
) -> None:
    shown = api.get("/app/ui", headers=bearer(preview_token())).json()
    assert shown["release"] == {"version": 1}
    assert [n["page"] for n in shown["nav"]] == ["home", "sites", "legal"]

    # what publish records for the same draft (publish.publish, step 4), read as legal L1 does
    wf, compiled = publish._compile("pilot-local", state["draft"])
    live = ReleaseRow(
        version=1,
        deployment_id="dep-1",
        **publish.release_values(wf, compiled, [], 1),
        published_by=None,
        published_at=datetime(2026, 11, 14, tzinfo=UTC),
        active=True,
    )
    assert ui.visible_ui("Pilot-Local", live, {"legal": 1}).model_dump(mode="json") == shown


def test_a_bd_l1_preview_opens_the_start_form_and_starting_is_read_only(
    api: TestClient, state: dict[str, Any]
) -> None:
    """#170: the role's start form comes from the draft as for a live BD L1; submitting it under
    the preview token is the usual read-only 403. Legal L1 may not start a site: no form."""
    bd1 = bearer(preview_token(**{"as": {"department": "bd", "level": 1}, "label": "BD L1"}))
    (flow,) = api.get("/app/ui", headers=bd1).json()["flows"]
    required = flow["start_form"]["schema"]["required"]
    assert required == ["site_name", "city", "address", "store_model"]
    (legal,) = api.get("/app/ui", headers=bearer(preview_token())).json()["flows"]
    assert legal["start_form"] is None

    values = {"site_name": "Indiranagar", "city": "Bengaluru", "address": "12th Main"}
    out = api.post("/app/cases", json={"flow": "store_launch", "values": values}, headers=bd1)
    assert out.status_code == 403
    assert out.json()["detail"] == "A preview is read-only"


def test_an_unchanged_draft_previews_as_the_live_release() -> None:
    first = preview.build("pilot-local", matrix_retail())
    live = ReleaseRow(**{**first.__dict__, "version": 4, "active": True})
    assert preview.build("pilot-local", matrix_retail(), active=live, version=5) is live
    changed = preview.build("pilot-local", with_field(), active=live, version=5)
    assert changed.version == 5 and changed.workflow["fields"]["dd_extra"]["label"] == "Extra check"


def test_the_token_helper_signs_what_the_routes_read() -> None:
    out = auth.preview_token(uuid.uuid4(), "pilot-local", LEGAL_L1)
    claims = jwt.decode(out.access_token, JWT_SECRET, algorithms=["HS256"])
    assert (claims["kind"], claims["as"], claims["label"]) == (
        "preview",
        {"department": "legal", "level": 1},
        "Legal L1",
    )
