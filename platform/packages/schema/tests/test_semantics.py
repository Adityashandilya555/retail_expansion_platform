import copy
from typing import Any

import pytest
from pydantic import ValidationError

from zm_schema import WorkflowV1, group_id, validate


def test_two_flows_are_valid(two_flows: dict[str, Any]) -> None:
    assert validate(two_flows) == []


def test_same_task_id_in_both_flows_fails_at_second_occurrence(two_flows: dict[str, Any]) -> None:
    two_flows["flows"][1]["stages"][0]["tasks"][0]["id"] = "bd_qualify"
    errors = validate(two_flows)
    assert [e.path for e in errors] == ["flows[1].stages[0].tasks[0].id"]


def test_all_errors_are_reported(minimal: dict[str, Any]) -> None:
    minimal["fields"]["Bad"] = {"type": "text", "label": "Bad"}
    minimal["flows"][0]["start"]["performers"][0]["department"] = "ops"
    minimal["apps"] = {"nav": [{"page": "x", "label": "X", "visible_to": ["nobody"]}]}
    paths = {e.path for e in validate(minimal)}
    assert paths == {
        "fields.Bad",
        "flows[0].start.performers[0].department",
        "apps.nav[0].page",
        "apps.nav[0].visible_to[0]",
    }


def test_constructing_an_invalid_model_raises(minimal: dict[str, Any]) -> None:
    minimal["flows"][0]["stages"][0]["tasks"][0]["show"] = ["monthly_rent"]
    with pytest.raises(ValidationError, match="not filled by an earlier step"):
        WorkflowV1.model_validate(minimal)


def _two_task_stage(minimal: dict[str, Any], *, parallel: bool) -> dict[str, Any]:
    doc = copy.deepcopy(minimal)
    stage = doc["flows"][0]["stages"][0]
    first = stage["tasks"][0]
    first.pop("show")
    second = {
        "id": "bd_review",
        "name": "Review",
        "performer": {"department": "bd", "level": 1},
        "show": ["monthly_rent"],
    }
    stage["tasks"] = [{"parallel": [first, second]}] if parallel else [first, second]
    return doc


def test_show_from_earlier_task_in_same_stage(minimal: dict[str, Any]) -> None:
    assert validate(_two_task_stage(minimal, parallel=False)) == []


def test_show_from_parallel_sibling_is_not_earlier(minimal: dict[str, Any]) -> None:
    errors = validate(_two_task_stage(minimal, parallel=True))
    assert [e.path for e in errors] == ["flows[0].stages[0].tasks[0].parallel[1].show[0]"]


def test_show_from_start_form_and_ancestor_stage(two_flows: dict[str, Any]) -> None:
    # lg_dd shows monthly_rent (filled in bd_qualification) and site_name (start form)
    two_flows["flows"][0]["stages"][1]["tasks"][0]["show"] = ["monthly_rent", "site_name"]
    assert validate(two_flows) == []


def test_show_from_unrelated_stage_is_not_earlier(two_flows: dict[str, Any]) -> None:
    two_flows["flows"][0]["stages"][1]["after"] = []
    errors = validate(two_flows)
    assert [e.path for e in errors] == ["flows[0].stages[1].tasks[0].show[0]"]


def test_terminal_outcomes_and_routes_in_stage(minimal: dict[str, Any]) -> None:
    t = minimal["flows"][0]["stages"][0]["tasks"][0]
    t["outcomes"] = [
        {"id": "close", "label": "Close", "to": "@close"},
        {"id": "end", "label": "End", "to": "@end"},
    ]
    t["route"] = [{"when": "monthly_rent > 0", "to": "bd_qualify", "label": "Again"}]
    assert validate(minimal) == []
    t["route"][0]["to"] = "@close"
    assert [e.path for e in validate(minimal)] == ["flows[0].stages[0].tasks[0].route[0].to"]


def test_rule_and_section_levels_are_checked(minimal: dict[str, Any]) -> None:
    t = minimal["flows"][0]["stages"][0]["tasks"][0]
    t["skip_if"] = {"initiator_at_least": {"department": "bd", "level": 5}}
    section = minimal["forms"]["qualify"]["sections"][0]
    section["hidden_below_level"] = {"department": "legal", "level": 1}
    paths = [e.path for e in validate(minimal)]
    assert paths == [
        "forms.qualify.sections[0].hidden_below_level.department",
        "flows[0].stages[0].tasks[0].skip_if.initiator_at_least.level",
    ]


def test_duplicates_and_layout_checks(two_flows: dict[str, Any]) -> None:
    two_flows["org"]["departments"].append(copy.deepcopy(two_flows["org"]["departments"][0]))
    two_flows["flows"][1]["key"] = "site_launch"
    two_flows["flows"][0]["stages"][1]["key"] = "bd_qualification"
    two_flows["apps"]["pages"].append(
        {"key": "sites", "layout": "department", "department": "ops", "flow": "nope"}
    )
    two_flows["kpis"] = [
        {"key": "k", "label": "K", "type": "count", "from": "nope"},
        {"key": "k", "label": "K2", "type": "duration", "to": "legal_dd"},
    ]
    paths = {e.path for e in validate(two_flows)}
    assert {
        "org.departments[2].key",
        "flows[1].key",
        "flows[0].stages[1].key",
        "apps.pages[1].key",
        "apps.pages[1].department",
        "apps.pages[1].flow",
        "kpis[0].from",
        "kpis[1].key",
    } <= paths


def test_row_span_and_document_rule(minimal: dict[str, Any]) -> None:
    minimal["fields"]["loi"] = {"type": "document", "label": "LOI"}
    row = minimal["forms"]["qualify"]["sections"][0]["rows"][0]
    row.append({"field": "site_name", "span": 8})
    paths = [e.path for e in validate(minimal)]
    assert paths == ["fields.loi.document", "forms.qualify.sections[0].rows[0]"]


def test_approval_chain_and_parallel_approval(minimal: dict[str, Any]) -> None:
    t = minimal["flows"][0]["stages"][0]["tasks"][0]
    t["approval"] = {"chain": [{"department": "legal", "level": 1}]}
    assert [e.path for e in validate(minimal)] == [
        "flows[0].stages[0].tasks[0].approval.chain[0].department"
    ]


def test_branch_target_inside_parallel_group(minimal: dict[str, Any]) -> None:
    stage = minimal["flows"][0]["stages"][0]
    first = stage["tasks"][0]
    first.pop("show")
    p1 = {"id": "p_one", "name": "One", "performer": {"initiator": True}}
    p2 = {"id": "p_two", "name": "Two", "performer": {"initiator": True}}
    first["outcomes"] = [{"id": "x", "label": "X", "to": "p_one"}]
    stage["tasks"] = [first, {"parallel": [p1, p2]}]
    errors = validate(minimal)
    assert [e.path for e in errors] == ["flows[0].stages[0].tasks[0].outcomes[0].to"]
    assert "parallel group" in errors[0].message


def test_unknown_form_and_show_field(minimal: dict[str, Any]) -> None:
    t = minimal["flows"][0]["stages"][0]["tasks"][0]
    t["form"] = "missing"
    t["show"] = ["nope"]
    paths = [e.path for e in validate(minimal)]
    assert paths == ["flows[0].stages[0].tasks[0].form", "flows[0].stages[0].tasks[0].show[0]"]


def test_group_id() -> None:
    assert group_id("legal", 2) == "legal__L2"
