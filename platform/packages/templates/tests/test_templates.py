import json
from pathlib import Path
from typing import Any

import pytest

from zm_schema import WorkflowV1, migrate, validate

TEMPLATES = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted(TEMPLATES.glob("*/workflow.json"))


def test_both_templates_present() -> None:
    assert {p.parent.name for p in WORKFLOWS} == {"matrix-retail", "qsr"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.parent.name)
def test_template_is_a_valid_workflow(path: Path) -> None:
    doc = json.loads(path.read_text())
    assert validate(doc) == []
    assert isinstance(migrate(doc), WorkflowV1)


def stage_keys(doc: dict[str, Any]) -> list[str]:
    return [s["key"] for f in doc["flows"] for s in f["stages"]]


def test_qsr_differs_from_matrix_retail(matrix_retail: dict, qsr: dict) -> None:
    assert not set(stage_keys(qsr)) & set(stage_keys(matrix_retail))
    assert qsr["flows"][0]["name"] != matrix_retail["flows"][0]["name"]
    assert qsr["flows"][0]["case_code"] != matrix_retail["flows"][0]["case_code"]
    # QSR's shape (local isolation client): BD -> Legal || Design -> Budget, Legal has 3 levels
    after = {s["key"]: s.get("after", []) for s in qsr["flows"][0]["stages"]}
    assert after == {
        "site_hunt": [],
        "statutory": ["site_hunt"],
        "store_design": ["site_hunt"],
        "capex": ["statutory", "store_design"],
    }
    legal = next(d for d in qsr["org"]["departments"] if d["key"] == "legal")
    assert [lv["n"] for lv in legal["levels"]] == [1, 2, 3]


def test_matrix_retail_apps(matrix_retail: dict) -> None:
    apps = matrix_retail["apps"]
    assert (apps["theme"], apps["accent"]) == ("quartz", "slate")
    pages = {p["key"]: p for p in apps["pages"]}
    assert pages["site"]["layout"] == "case" and pages["site"]["flow"] == "store_launch"
    depts = {d["key"] for d in matrix_retail["org"]["departments"]}
    stage_depts = {s["department"] for s in matrix_retail["flows"][0]["stages"]}
    # one page per department that runs a stage, visible to that department and management
    assert stage_depts <= set(pages)
    nav = {n["page"]: n["visible_to"] for n in apps["nav"]}
    for dept in stage_depts:
        assert nav[dept] == [dept, "management"]
    assert stage_depts <= depts


def test_demo_users(matrix_retail: dict) -> None:
    users = json.loads((TEMPLATES / "matrix-retail" / "demo-users.json").read_text())["users"]
    assert all(set(u) == {"email", "name", "department", "level"} for u in users)  # no passwords
    levels = {
        d["key"]: {lv["n"] for lv in d["levels"]} for d in matrix_retail["org"]["departments"]
    }
    assert [(u["department"], u["level"]) for u in users] == [
        ("bd", 1),
        ("bd", 2),
        ("legal", 1),
        ("legal", 2),
        ("management", 1),
    ]
    for u in users:
        assert u["level"] in levels[u["department"]]
        assert u["email"].endswith(".demo")
