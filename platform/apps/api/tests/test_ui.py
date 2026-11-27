"""`GET /app/ui` (#27): the active release's UI, filtered to the user's departments by the API,
with each flow's start form for the users who may start it (#170)."""

import copy
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zm_api import preview, releases, tasks, ui
from zm_api.errors import Unauthorized
from zm_api.main import create_app
from zm_api.registry import Client
from zm_api.releases import ReleaseRow
from zm_api.templates import load_templates
from zm_schema import migrate

from .conftest import bearer, token

START_FIELDS = ["site_name", "city", "address", "store_model"]


def matrix_retail() -> dict[str, Any]:
    wf = next(t.workflow for t in load_templates() if t.key == "matrix-retail")
    return migrate(copy.deepcopy(wf)).model_dump(mode="json", by_alias=True, exclude_unset=True)


def release(version: int = 1, workflow: dict[str, Any] | None = None) -> ReleaseRow:
    wf = workflow or matrix_retail()
    full = migrate(wf).apps.model_dump(mode="json", by_alias=True)
    return ReleaseRow(
        version=version,
        deployment_id=f"dep-{version}",
        workflow=wf,
        forms=preview.build("pilot-local", wf).forms,  # the compiled forms, as publish stores them
        ui=full,
        routing_vars={},
        process_keys={},
        blocks_used={},
        sha256="x",
        published_by=None,
        published_at=datetime(2026, 10, 22, tzinfo=UTC),
        active=True,
    )


def keys(out: ui.AppUi) -> tuple[list[str], list[str]]:
    return [n.page for n in out.nav], [p.key for p in out.pages]


# --- the filter --------------------------------------------------------------------------------


def test_a_legal_user_gets_the_legal_page_and_everyones_pages() -> None:
    out = ui.visible_ui("Matrix Retail", release(), {"legal": 1})
    nav, pages = keys(out)
    assert nav == ["home", "sites", "legal"]
    assert pages == ["home", "sites", "legal", "site"]  # `site` (case layout) has no nav item
    legal = next(p for p in out.pages if p.key == "legal")
    assert legal.layout == "standard"
    assert [b.block for b in legal.regions["main"]] == ["task_inbox", "case_table"]


def test_a_bd_only_user_never_receives_the_legal_nav_item_or_page() -> None:
    nav, pages = keys(ui.visible_ui("Matrix Retail", release(), {"bd": 1}))
    assert "legal" not in nav and "legal" not in pages
    assert "bd" in nav and "bd" in pages


def test_management_sees_every_department_page() -> None:
    nav, _ = keys(ui.visible_ui("Matrix Retail", release(), {"management": 1}))
    assert nav == [
        "home",
        "sites",
        "bd",
        "legal",
        "design",
        "pe",
        "project",
        "nso",
        "management",
    ]


def test_a_user_without_memberships_sees_only_pages_for_everyone() -> None:
    nav, pages = keys(ui.visible_ui("Matrix Retail", release(), {}))
    assert nav == ["home", "sites"]
    assert pages == ["home", "sites", "site"]


def test_client_look_release_version_and_flows() -> None:
    wf = matrix_retail()
    wf["flows"][0]["stages"][2]["sla_days"] = 5  # legal (S2-29)
    out = ui.visible_ui("Matrix Retail", release(version=3, workflow=wf), {"legal": 1})
    look = {"name": "Matrix Retail", "theme": "quartz", "accent": "slate"}
    assert out.client.model_dump() == look
    assert out.release.version == 3
    (flow,) = out.flows
    assert (flow.key, flow.case_label, flow.case_code) == ("store_launch", "Site", "MX-{seq:4}")
    assert flow.stages[0].model_dump() == {
        "key": "bd_qualification",
        "name": flow.stages[0].name,
        "department": "bd",
        "sla_days": None,
    }
    assert [s.department for s in flow.stages][:4] == ["bd", "bd", "legal", "bd"]
    assert flow.stages[2].sla_days == 5
    assert [f.key for f in flow.start_fields] == START_FIELDS
    assert flow.start_fields[0].type == "text" and flow.start_fields[0].label


def test_a_new_release_changes_the_page_with_no_frontend_change() -> None:
    wf = matrix_retail()
    legal = next(p for p in wf["apps"]["pages"] if p["key"] == "legal")
    legal["regions"]["main"].reverse()
    out = ui.visible_ui("Matrix Retail", release(version=2, workflow=wf), {"legal": 1})
    page = next(p for p in out.pages if p.key == "legal")
    assert [b.block for b in page.regions["main"]] == ["case_table", "task_inbox"]
    assert out.release.version == 2


# --- the start form (#170) ---------------------------------------------------------------------


def start_form(out: ui.AppUi) -> dict[str, Any] | None:
    """The store launch flow's `start_form` as the API sends it."""
    (flow,) = out.flows
    return None if flow.start_form is None else flow.start_form.model_dump(by_alias=True)


def test_bd_l1_and_above_get_the_start_form_start_case_validates() -> None:
    """Matrix Retail lets BD L1 and above start a site: the four start fields, all required."""
    shown = release()
    form = start_form(ui.visible_ui("Matrix Retail", shown, {"bd": 1}))
    assert form == tasks.visible_form(shown.forms["start:store_launch"], {"bd": 1})
    assert list(form["schema"]["properties"]) == START_FIELDS
    assert form["schema"]["required"] == START_FIELDS
    assert [s["key"] for s in form["layout"]] == ["site"]
    assert start_form(ui.visible_ui("Matrix Retail", shown, {"bd": 2, "legal": 1})) == form


def test_who_may_not_start_the_flow_gets_no_start_form() -> None:
    assert start_form(ui.visible_ui("Matrix Retail", release(), {"legal": 1})) is None
    assert start_form(ui.visible_ui("Matrix Retail", release(), {"legal": 2})) is None
    assert start_form(ui.visible_ui("Matrix Retail", release(), {})) is None


def test_the_start_form_drops_sections_hidden_below_the_users_level() -> None:
    wf = matrix_retail()
    wf["fields"]["site_lead_notes"] = {"type": "textarea", "label": "Lead notes"}
    wf["forms"]["start"]["sections"].append(
        {
            "key": "lead",
            "title": "Lead review",
            "hidden_below_level": {"department": "bd", "level": 2},
            "rows": [[{"field": "site_lead_notes"}]],
        }
    )
    shown = release(workflow=wf)
    l1 = start_form(ui.visible_ui("Matrix Retail", shown, {"bd": 1}))
    l2 = start_form(ui.visible_ui("Matrix Retail", shown, {"bd": 2}))
    assert l1 is not None and l2 is not None
    assert "site_lead_notes" not in l1["schema"]["properties"]
    assert [s["key"] for s in l1["layout"]] == ["site"]
    assert "site_lead_notes" in l2["schema"]["properties"]
    assert [s["key"] for s in l2["layout"]] == ["site", "lead"]


# --- the endpoint ------------------------------------------------------------------------------


class FakeCtx:
    def __init__(self, row: Client) -> None:
        self.client = row

    class db:  # noqa: N801
        @staticmethod
        @asynccontextmanager
        async def connect():  # noqa: ANN205
            yield None


class FakeResolver:
    def __init__(self, rows: dict[str, Client]) -> None:
        self.rows = rows

    async def resolve(self, slug: str) -> FakeCtx | None:
        row = self.rows.get(slug)
        return None if row is None else FakeCtx(row)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def api(rows: dict[str, Client]) -> TestClient:
    return TestClient(create_app(resolver=FakeResolver(rows)))  # type: ignore[arg-type]


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What the client's database holds: the active release and the user's levels."""
    state: dict[str, Any] = {"release": release(), "levels": {"legal": 1}}

    async def active(_conn: Any) -> ReleaseRow | None:
        return state["release"]

    async def person(_conn: Any, uid: uuid.UUID) -> tasks.Person:
        if state["levels"] is None:
            raise Unauthorized()
        return tasks.Person(uid, state["levels"])

    monkeypatch.setattr(releases, "active_release", active)
    monkeypatch.setattr(tasks, "person", person)
    return state


def test_app_ui_needs_a_client_token(api: TestClient) -> None:
    assert api.get("/app/ui").status_code == 401
    assert api.get("/app/ui", headers=bearer(token("owner", client=None))).status_code == 403


def test_app_ui_returns_the_filtered_release(api: TestClient, vault: dict[str, Any]) -> None:
    body = api.get("/app/ui", headers=bearer(token())).json()
    assert body["client"] == {"name": "Pilot-Local", "theme": "quartz", "accent": "slate"}
    assert body["release"] == {"version": 1}
    assert [n["page"] for n in body["nav"]] == ["home", "sites", "legal"]
    assert body["flows"][0]["case_label"] == "Site"

    vault["levels"] = {"bd": 1}
    body = api.get("/app/ui", headers=bearer(token())).json()
    assert "legal" not in [p["key"] for p in body["pages"]]


def test_app_ui_sends_the_start_form_only_to_who_may_start_the_flow(
    api: TestClient, vault: dict[str, Any]
) -> None:
    """The release row as published before #170 holds no start form in its UI: it is read from
    the release's forms on every request, so an existing release gets it with no republish."""
    assert "start_form" not in json.dumps(vault["release"].ui)
    vault["levels"] = {"bd": 1}
    (flow,) = api.get("/app/ui", headers=bearer(token())).json()["flows"]
    assert list(flow["start_form"]["schema"]["properties"]) == START_FIELDS
    assert [s["key"] for s in flow["start_form"]["layout"]] == ["site"]
    assert [f["key"] for f in flow["start_fields"]] == START_FIELDS  # kept for the other blocks

    vault["levels"] = {"legal": 1}
    (flow,) = api.get("/app/ui", headers=bearer(token())).json()["flows"]
    assert flow["start_form"] is None


def test_app_ui_before_the_first_publish_is_404(api: TestClient, vault: dict[str, Any]) -> None:
    vault["release"] = None
    response = api.get("/app/ui", headers=bearer(token()))
    assert response.status_code == 404
    assert response.json()["detail"] == "Nothing has been published yet"


def test_app_ui_for_a_deactivated_user_is_401(api: TestClient, vault: dict[str, Any]) -> None:
    vault["levels"] = None
    assert api.get("/app/ui", headers=bearer(token())).status_code == 401
