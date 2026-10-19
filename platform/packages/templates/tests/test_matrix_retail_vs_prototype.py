"""Matrix Retail (#18) keeps the prototype's step list: stages, order, task ids and fields."""

import re
from typing import Any

# D4: prototype roles -> (department, level)
ROLES = {
    "bdExecutive": ("bd", 1),
    "bdSupervisor": ("bd", 2),
    "legalExecutive": ("legal", 1),
    "legalSupervisor": ("legal", 2),
    "designExecutive": ("design", 1),
    "designSupervisor": ("design", 2),
    "peExecutive": ("pe", 1),
    "peSupervisor": ("pe", 2),
    "projectExecutive": ("project", 1),
    "projectSupervisor": ("project", 2),
    "nsoExecutive": ("nso", 1),
    "nsoSupervisor": ("nso", 2),
    "businessAdmin": ("management", 1),
}


def snake(name: str) -> str:
    """The only rename rule: camelCase field ids become snake_case keys."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def task_seq(items: list[dict[str, Any]]) -> list[str]:
    return [t["id"] for i in items for t in (i["parallel"] if "parallel" in i else [i])]


def tasks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for i in items for t in (i["parallel"] if "parallel" in i else [i])]


def test_stages_and_order(matrix_retail: dict, prototype: dict) -> None:
    stages = matrix_retail["flows"][0]["stages"]
    assert [(s["key"], s["after"]) for s in stages] == [
        (m["key"], m.get("after", [])) for m in prototype["modules"]
    ]


def test_task_ids_and_order(matrix_retail: dict, prototype: dict) -> None:
    for stage, module in zip(
        matrix_retail["flows"][0]["stages"], prototype["modules"], strict=True
    ):
        assert task_seq(stage["tasks"]) == task_seq(module["tasks"]), stage["key"]
        groups = [len(i["parallel"]) for i in stage["tasks"] if "parallel" in i]
        assert groups == [len(i["parallel"]) for i in module["tasks"] if "parallel" in i]


def test_performers_follow_roles(matrix_retail: dict, prototype: dict) -> None:
    for stage, module in zip(
        matrix_retail["flows"][0]["stages"], prototype["modules"], strict=True
    ):
        for ours, theirs in zip(tasks(stage["tasks"]), tasks(module["tasks"]), strict=True):
            a = theirs.get("assignee")
            if a == "initiator":
                assert ours["performer"] == {"initiator": True}
            elif a:  # the prototype's BPMN uses the assignee instead of the role
                assert ours["performer"] == {"from_field": snake(a)}
            else:
                dept, level = ROLES[theirs["role"]]
                assert ours["performer"] == {"department": dept, "level": level}


def test_every_prototype_field_is_kept(matrix_retail: dict, prototype: dict) -> None:
    ids = {f["id"] for f in prototype["app"]["start"]["fields"]}
    for module in prototype["modules"]:
        for t in tasks(module["tasks"]):
            ids |= {f["id"] for f in t.get("fields", [])}
    assert {snake(i) for i in ids} == set(matrix_retail["fields"])


def test_start_form(matrix_retail: dict) -> None:
    rows = matrix_retail["forms"]["start"]["sections"][0]["rows"]
    assert [r[0]["field"] for r in rows] == ["site_name", "city", "address", "store_model"]


def test_file_fields_become_optional_documents(matrix_retail: dict, prototype: dict) -> None:
    files = {
        snake(f["id"])
        for m in prototype["modules"]
        for t in tasks(m["tasks"])
        for f in t.get("fields", [])
        if f["type"] == "file"
    }
    assert files
    for key in files:
        field = matrix_retail["fields"][key]
        assert field["type"] == "document" and field["required"] is False  # D10


def test_approvals_keep_send_back_targets(matrix_retail: dict, prototype: dict) -> None:
    for stage, module in zip(
        matrix_retail["flows"][0]["stages"], prototype["modules"], strict=True
    ):
        for ours, theirs in zip(tasks(stage["tasks"]), tasks(module["tasks"]), strict=True):
            if theirs.get("send_back"):
                back = [o for o in ours["outcomes"] if o["id"] == "send_back"]
                assert back and back[0]["to"] == theirs["send_back"], ours["id"]
            if theirs.get("skip_if") == {"initiator_in": "bdSupervisor"}:
                assert ours["skip_if"] == {"initiator_at_least": {"department": "bd", "level": 2}}
