"""The cases read API without the stack (#91): stage states from history rows, the activity
timeline (#94), status, title, masking, task names, the routes' guards and the two Operaton
history calls. The full flow runs in test_cases_integration.py (`-m integration`)."""

import asyncio
import copy
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from zm_api import cases, cases_routes, tasks
from zm_api.context import Resolver
from zm_api.main import create_app
from zm_api.operaton import OperatonClient, time_of
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


AT = datetime(2026, 10, 26, 10, tzinfo=UTC)  # "2026-10-26T10:00:00.000+0000" as Operaton sends it


def test_operaton_times_become_iso_utc() -> None:
    """Operaton's `+0000` offset has no colon, which Safari's `Date.parse` rejects: the API
    sends ISO 8601 in UTC instead (a `+0530` engine too)."""
    assert time_of("2026-10-18T09:00:00.000+0000") == datetime(2026, 10, 18, 9, tzinfo=UTC)
    assert time_of("2026-10-18T14:30:00.000+0530") == datetime(2026, 10, 18, 9, tzinfo=UTC)
    stage = cases_routes.StageOut(
        key="legal",
        name="Legal",
        department="legal",
        state="open",
        started_at=time_of("2026-10-18T09:00:00.000+0000"),
        ended_at=None,
    )
    sent = json.loads(stage.model_dump_json())
    assert sent["started_at"] == "2026-10-18T09:00:00Z" and sent["ended_at"] is None


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
    assert found[0].started_at == found[0].ended_at == AT

    finished = [*cancelled[:2], call("legal", t, t, canceled=True), call("finance", t, t)]
    assert states(cases.stage_states(STAGES, variables, finished))["finance"] == "done"


def test_open_stage_from_activities_has_no_end() -> None:
    t = "2026-10-26T10:00:00.000+0000"
    found = cases.stage_states(STAGES, {"case_status": "open"}, [call("bd_qualification", t)])
    assert found[0].state == "open" and found[0].started_at == AT and found[0].ended_at is None


def test_without_activities_a_stage_starts_when_its_variable_was_first_set() -> None:
    """The list (S2-29: days in stage, overdue): `m_<stage>`'s history row keeps the createTime
    of its first value, `in progress`, set when the stage started."""
    first, later = "2026-10-20T09:00:00.000+0000", "2026-10-26T09:00:00.000+0000"
    variables = {"case_status": "open", "m_bd_qualification": "done", "m_legal": "in progress"}
    started = {"case_status": first, "m_bd_qualification": first, "m_legal": later}
    found = {s.key: s for s in cases.stage_states(STAGES, variables, started=started)}
    assert found["bd_qualification"].started_at == datetime(2026, 10, 20, 9, tzinfo=UTC)
    assert found["legal"].started_at == datetime(2026, 10, 26, 9, tzinfo=UTC)
    assert found["bd_qualification"].ended_at is None  # only the activities' history has ends
    assert found["design"].started_at is None


def test_events_lead_with_created_then_stage_runs_and_submissions_by_time() -> None:
    """The Activity tab (S2-29). The case row is written once Operaton has started the case, so
    `created` trails the first stage's start yet leads; a cancelled run ends `stage_closed`."""

    def t(minute: int) -> str:
        return f"2026-10-26T10:{minute:02d}:00.000+0000"

    by = uuid.uuid4()
    created = cases.Event("created", datetime.fromisoformat(t(1)), by=by, by_name="Bd2")
    sent = cases.Event(
        "submitted",
        datetime.fromisoformat(t(2)),
        stage="bd_qualification",
        task="bd_send_legal",
        task_name="Send to Legal",
        by=by,
        by_name="Bd2",
    )
    runs = [
        call("bd_qualification", t(0), t(3)),
        call("legal", t(3)),
        call("finance", t(3), t(4), canceled=True),  # cut short by a close
    ]
    found = cases.events(created, runs, [sent])
    assert [(e.kind, e.stage) for e in found] == [
        ("created", None),
        ("stage_started", "bd_qualification"),
        ("submitted", "bd_qualification"),
        ("stage_done", "bd_qualification"),
        ("stage_started", "legal"),
        ("stage_started", "finance"),
        ("stage_closed", "finance"),
    ]
    assert found[2] is sent and found[3].at == datetime.fromisoformat(t(3))
    assert cases.events(created, [], []) == [created]


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
    fields = doc["fields"]
    l1 = cases.sections(tasks.visible_form(form, {"bd": 1}), values, fields, fields)
    assert "deal" not in [s.key for s in l1]
    l2 = cases.sections(tasks.visible_form(form, {"bd": 2}), values, fields, fields)
    deal = next(s for s in l2 if s.key == "deal")
    assert deal.fields == [
        {
            "key": "deal_notes",
            "label": "Deal notes",
            "type": "textarea",
            "value": "secret",
            "since": None,
        }
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


def test_a_cut_off_list_says_so_in_a_header_the_browser_can_read(
    rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def capped(*_: Any) -> tuple[list[cases.CaseRow], bool]:
        return [], True

    web = "https://zm-web.test"
    monkeypatch.setattr(cases, "list_cases", capped)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", web)
    with TestClient(create_app(Resolver(FakeRegistry(rows)))) as http:
        r = http.get("/app/cases", headers={**bearer(token()), "Origin": web})
    assert r.status_code == 200 and r.json() == []
    assert r.headers["x-truncated"] == "true"
    assert "x-truncated" in r.headers["access-control-expose-headers"].lower()


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


class FakeEngine:
    def __init__(self, rows_per_instance: int) -> None:
        self.calls: list[tuple[int, int]] = []
        self.per = rows_per_instance

    async def history_variables(
        self, ids: list[str], names: list[str], *, max_results: int
    ) -> list:
        self.calls.append((len(ids), max_results))
        return [{"processInstanceId": i, "name": "case_status"} for i in ids] * self.per


def test_history_is_read_in_chunks_and_a_full_page_is_an_error() -> None:
    from types import SimpleNamespace

    engine = FakeEngine(1)
    ctx = SimpleNamespace(operaton=engine)
    ids = [f"p{i}" for i in range(cases.HISTORY_CHUNK * 2 + 1)]
    found = asyncio.run(cases.history_variables(ctx, ids, ["case_status", "m_legal"]))  # type: ignore[arg-type]
    assert len(found) == len(ids)
    full_page = cases.HISTORY_CHUNK * 2 + 1  # 2 names per instance, plus the one-too-many row
    assert engine.calls == [
        (cases.HISTORY_CHUNK, full_page),
        (cases.HISTORY_CHUNK, full_page),
        (1, 3),
    ]
    one = FakeEngine(1)
    page = ids[: cases.MAX_CASES + 1]  # a listing reads the newest 500 and one more
    asyncio.run(cases.history_variables(SimpleNamespace(operaton=one), page, ["x"]))  # type: ignore[arg-type]
    assert one.calls == [(cases.MAX_CASES + 1, cases.MAX_CASES + 2)]  # a single request

    full = SimpleNamespace(operaton=FakeEngine(2))  # more rows than 1 name allows: not trusted
    with pytest.raises(RuntimeError, match="passed"):
        asyncio.run(cases.history_variables(full, ids[:10], ["x"]))  # type: ignore[arg-type]
