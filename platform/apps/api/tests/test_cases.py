"""The cases read API without the stack (#91): stage states from history rows, status, title,
masking, task names, the routes' guards and the two Operaton history calls. The full flow runs in
test_cases_integration.py (`-m integration`)."""

import asyncio
import copy
import json
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from zm_api import cases, tasks
from zm_api.context import Resolver
from zm_api.main import create_app
from zm_api.operaton import OperatonClient
from zm_api.registry import Client
from zm_api.templates import load_templates

from .conftest import bearer, token


def matrix_retail() -> dict[str, Any]:
    return copy.deepcopy(next(t.workflow for t in load_templates() if t.key == "matrix-retail"))


STAGES = matrix_retail()["flows"][0]["stages"]


def states(found: list[cases.Stage]) -> dict[str, str]:
    return {s.key: s.state for s in found}


def test_a_case_at_legal_and_finance_has_both_open() -> None:
    variables = {
        "case_status": "open",
        "m_bd_qualification": "done",
        "m_bd_loi": "done",
        "m_legal": "in progress",
        "m_finance": "in progress",
    }
    found = states(cases.stage_states(STAGES, variables))
    assert found["bd_qualification"] == found["bd_loi"] == "done"
    assert found["legal"] == found["finance"] == "open"  # parallel after bd_loi
    assert {found[k] for k in ("design", "pe_budget", "project", "nso", "launch", "closure")} == {
        "queued"
    }


def test_a_new_case_is_open_in_its_first_stage_only() -> None:
    found = states(
        cases.stage_states(STAGES, {"case_status": "open", "m_bd_qualification": "in progress"})
    )
    assert found["bd_qualification"] == "open"
    assert set(found.values()) == {"open", "queued"}


def test_closed_in_legal_from_variables() -> None:
    """The stage end listener also fires on the close, so m_legal says done: case_status wins."""
    variables = {
        "case_status": "closed:legal",
        "m_bd_qualification": "done",
        "m_bd_loi": "done",
        "m_legal": "done",
        "m_finance": "done",
    }
    found = states(cases.stage_states(STAGES, variables))
    assert found["legal"] == "closed"
    assert found["design"] == found["closure"] == "queued"  # after legal
    assert cases.case_status(variables) == "closed"


def call(stage: str, start: str, end: str | None = None, canceled: bool = False) -> dict:
    return {"activityId": f"call_{stage}", "startTime": start, "endTime": end, "canceled": canceled}


def test_closed_with_activities_tells_cancelled_from_finished() -> None:
    variables = {"case_status": "closed:legal", "m_legal": "done", "m_finance": "done"}
    t = "2026-10-26T10:00:00.000+0000"
    cancelled = [
        call("bd_qualification", t, t),
        call("bd_loi", t, t),
        call("legal", t, t, canceled=True),
        call("finance", t, t, canceled=True),  # cut short by the close
    ]
    found = cases.stage_states(STAGES, variables, cancelled)
    assert states(found)["finance"] == "closed" and states(found)["legal"] == "closed"
    assert states(found)["bd_loi"] == "done" and states(found)["design"] == "queued"
    assert found[0].started_at == t and found[0].ended_at == t

    finished = [*cancelled[:2], call("legal", t, t, canceled=True), call("finance", t, t)]
    assert states(cases.stage_states(STAGES, variables, finished))["finance"] == "done"


def test_open_stage_from_activities_has_no_end() -> None:
    t = "2026-10-26T10:00:00.000+0000"
    found = cases.stage_states(STAGES, {"case_status": "open"}, [call("bd_qualification", t)])
    assert found[0].state == "open" and found[0].started_at == t and found[0].ended_at is None


def test_case_status() -> None:
    assert cases.case_status({}) == "open"
    assert cases.case_status({"case_status": "open"}) == "open"
    assert cases.case_status({"case_status": "completed"}) == "completed"
    assert cases.case_status({"case_status": "closed:finance"}) == "closed"


def test_title_is_the_first_text_start_field() -> None:
    doc = matrix_retail()
    flow = doc["flows"][0]
    assert (
        cases.title(doc, flow, {"site_name": "Indiranagar", "city": "Bengaluru"}) == "Indiranagar"
    )
    assert cases.title(doc, flow, {"city": "Bengaluru"}) == "Bengaluru"
    assert cases.title(doc, flow, {}) == ""


def test_task_names_follow_the_compiler() -> None:
    doc = matrix_retail()
    assert cases.task_name(doc, "legal_dd_checklist") == "Fill DD checklist"
    # an approval step `<task>__appr_<dept>_L<n>`, named like the compiler names it
    name = cases.task_name(doc, "fin_ca_entry__appr_management_L1")
    assert name == "Enter CA code, KYC and amount: Management L1 approval"
    assert cases.task_name(doc, "unknown") == "unknown"


def test_sections_show_only_what_the_level_sees() -> None:
    from zm_compiler import compile
    from zm_schema import migrate

    doc = matrix_retail()
    doc["fields"]["deal_notes"] = {"type": "textarea", "label": "Deal notes"}
    doc["forms"]["start"]["sections"].append(
        {
            "key": "deal",
            "title": "Deal",
            "hidden_below_level": {"department": "bd", "level": 2},
            "rows": [[{"field": "deal_notes"}]],
        }
    )
    spec = compile(migrate(doc), "pilot-local").forms["start:store_launch"]
    form = {"schema": spec.schema, "layout": spec.layout}
    values = {"site_name": "Indiranagar", "deal_notes": "secret"}
    l1 = cases.sections(tasks.visible_form(form, {"bd": 1}), values)
    assert "deal" not in [s.key for s in l1]
    l2 = cases.sections(tasks.visible_form(form, {"bd": 2}), values)
    deal = next(s for s in l2 if s.key == "deal")
    assert deal.fields == [
        {"key": "deal_notes", "label": "Deal notes", "type": "textarea", "value": "secret"}
    ]
    site = l1[0].fields[0]
    assert site["key"] == "site_name" and site["value"] == "Indiranagar"


class FakeRegistry:
    def __init__(self, rows: dict[str, Client]) -> None:
        self.rows = rows

    async def __call__(self, slug: str) -> Client | None:
        return self.rows.get(slug)


def test_routes_need_a_client_token(rows: dict[str, Client]) -> None:
    with TestClient(create_app(Resolver(FakeRegistry(rows)))) as http:
        assert http.get("/app/cases").status_code == 401
        assert http.get("/app/cases/MX-0001").status_code == 401
        owner = bearer(token("owner", client=None))
        assert http.get("/app/cases", headers=owner).status_code == 403
        assert http.get("/app/cases/MX-0001", headers=owner).status_code == 403


@respx.mock
def test_the_history_calls() -> None:
    asyncio.run(_history_calls())


async def _history_calls() -> None:
    base = "http://operaton.test/engine-rest"
    op = OperatonClient(base, auth=("zm-service", "pw"), client="pilot-local")
    variables = respx.post(f"{base}/history/variable-instance").mock(
        return_value=httpx.Response(200, json=[{"name": "case_status", "value": "open"}])
    )
    activities = respx.get(
        f"{base}/history/activity-instance",
        params={"processInstanceId": "p1", "activityType": "callActivity"},
    ).mock(return_value=httpx.Response(200, json=[{"activityId": "call_legal"}]))
    try:
        found = await op.history_variables(["p1", "p2"], ["case_status", "m_legal"])
        assert found == [{"name": "case_status", "value": "open"}]
        assert json.loads(variables.calls[0].request.content) == {
            "processInstanceIdIn": ["p1", "p2"],
            "variableNameIn": ["case_status", "m_legal"],
        }
        assert await op.history_variables([], ["x"]) == []  # nothing to ask: no call
        assert await op.history_activities("p1") == [{"activityId": "call_legal"}]
    finally:
        await op.aclose()
    assert variables.call_count == 1 and activities.called


def test_masked_start_values_follow_the_start_form_only() -> None:
    """A field hidden on the start form stays hidden even if a task form shows it."""
    from zm_compiler import compile
    from zm_schema import migrate

    doc = matrix_retail()
    doc["fields"]["deal_notes"] = {"type": "textarea", "label": "Deal notes"}
    doc["forms"]["start"]["sections"].append(
        {
            "key": "deal",
            "title": "Deal",
            "hidden_below_level": {"department": "bd", "level": 2},
            "rows": [[{"field": "deal_notes"}]],
        }
    )
    doc["forms"]["bd_site_details"]["sections"].append(
        {"key": "deal_again", "title": "Deal", "rows": [[{"field": "deal_notes"}]]}
    )
    spec = compile(migrate(doc), "pilot-local").forms["start:store_launch"]
    form = {"schema": spec.schema, "layout": spec.layout}
    values = {"site_name": "Indiranagar", "deal_notes": "secret"}
    assert cases.masked_start_values(form, values, {"legal": 1}) == {"site_name": "Indiranagar"}
    assert cases.masked_start_values(form, values, {"bd": 2}) == values
    assert cases.masked_start_values(None, values, {"bd": 2}) == {}
