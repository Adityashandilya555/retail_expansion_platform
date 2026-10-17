"""Regenerate `tests/broken/NN-<what>.json` from the valid fixtures (run by hand, not in CI).

Each case breaks one rule of a valid document. The output file holds the expected error path, a
substring of the expected message and the broken document. The script refuses to write a case
that doesn't fail with exactly that one error, so the samples stay focused.

    uv run python packages/schema/tests/make_broken.py
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from zm_schema import validate

HERE = Path(__file__).parent
Doc = dict[str, Any]


def load(name: str) -> Doc:
    return json.loads((HERE / "valid" / f"{name}.json").read_text())


def task(doc: Doc, flow: int = 0, stage: int = 0, i: int = 0) -> Doc:
    return doc["flows"][flow]["stages"][stage]["tasks"][i]


def _dup_task_id(d: Doc) -> None:
    task(d, flow=1)["id"] = "bd_qualify"


def _outcome_missing(d: Doc) -> None:
    task(d)["outcomes"] = [{"id": "back", "label": "Back", "to": "bd_nowhere"}]


def _outcome_other_stage(d: Doc) -> None:
    task(d, stage=1)["outcomes"] = [{"id": "back", "label": "Back", "to": "bd_qualify"}]


def _parallel_branch(d: Doc) -> None:
    stage = d["flows"][0]["stages"][0]
    first = stage["tasks"][0]
    first["outcomes"] = [{"id": "drop", "label": "Drop", "to": "@close"}]
    second = {"id": "bd_photos", "name": "Site photos", "performer": {"initiator": True}}
    stage["tasks"] = [{"parallel": [first, second]}]


def _after_unknown(d: Doc) -> None:
    stage = d["flows"][0]["stages"][1]
    stage["after"] = ["bd_nowhere"]
    stage["tasks"][0].pop("show")


def _after_cycle(d: Doc) -> None:
    d["flows"][0]["stages"][0]["after"] = ["legal_dd"]


def _unknown_department(d: Doc) -> None:
    d["flows"][0]["start"]["performers"][0]["department"] = "ops"


def _unknown_level(d: Doc) -> None:
    task(d)["performer"] = {"department": "bd", "level": 3}


def _from_field_not_user(d: Doc) -> None:
    task(d)["performer"] = {"from_field": "site_name"}


def _form_unknown_field(d: Doc) -> None:
    d["forms"]["qualify"]["sections"][0]["rows"][0][0]["field"] = "rent"


def _show_not_earlier(d: Doc) -> None:
    task(d)["show"] = ["monthly_rent"]


def _bad_field_key(d: Doc) -> None:
    d["fields"]["SiteCode"] = {"type": "text", "label": "Site code"}


def _duplicate_option(d: Doc) -> None:
    opts = [{"id": "cafe", "label": "Cafe"}, {"id": "cafe", "label": "Cafe+"}]
    d["fields"]["store_model"] = {"type": "select", "label": "Store model", "options": opts}


def _select_without_options(d: Doc) -> None:
    d["fields"]["store_model"] = {"type": "select", "label": "Store model"}


def _levels_gap(d: Doc) -> None:
    d["org"]["departments"][0]["levels"].append({"n": 3, "name": "BD Head"})


def _bad_group_id(d: Doc) -> None:
    d["org"]["departments"].append(
        {"key": "ops team", "name": "Operations", "levels": [{"n": 1, "name": "Ops"}]}
    )


def _case_page_without_flow(d: Doc) -> None:
    d["apps"] = {"pages": [{"key": "site", "layout": "case"}]}


def _nav_unknown_page(d: Doc) -> None:
    d["apps"] = {"nav": [{"page": "sites", "label": "Sites", "visible_to": ["*"]}]}


def _send_back_unknown(d: Doc) -> None:
    approval = {"chain": [{"department": "bd", "level": 1}], "send_back_to": "bd_nowhere"}
    task(d)["approval"] = approval


def _wrong_field_type(d: Doc) -> None:
    d["fields"]["site_name"]["type"] = "phone"


P = "flows[0].stages[0].tasks[0]"
CASES: list[tuple[str, str, Callable[[Doc], None], str, str]] = [
    # (name, base fixture, mutation, expected path, expected message substring)
    (
        "duplicate-task-id-across-flows",
        "two_flows",
        _dup_task_id,
        "flows[1].stages[0].tasks[0].id",
        "already used at flows[0].stages[0].tasks[0].id",
    ),
    (
        "outcome-target-missing",
        "minimal",
        _outcome_missing,
        f"{P}.outcomes[0].to",
        "is not a task in stage",
    ),
    (
        "outcome-target-in-other-stage",
        "two_flows",
        _outcome_other_stage,
        "flows[0].stages[1].tasks[0].outcomes[0].to",
        "is not a task in stage 'legal_dd'",
    ),
    (
        "branch-inside-parallel-group",
        "minimal",
        _parallel_branch,
        "flows[0].stages[0].tasks[0].parallel[0]",
        "cannot branch",
    ),
    (
        "after-unknown-stage",
        "two_flows",
        _after_unknown,
        "flows[0].stages[1].after[0]",
        "unknown stage 'bd_nowhere'",
    ),
    ("after-cycle", "two_flows", _after_cycle, "flows[0].stages", "cycle"),
    (
        "performer-unknown-department",
        "minimal",
        _unknown_department,
        "flows[0].start.performers[0].department",
        "unknown department 'ops'",
    ),
    (
        "performer-unknown-level",
        "minimal",
        _unknown_level,
        f"{P}.performer.level",
        "has no level 3",
    ),
    (
        "from-field-not-a-user-field",
        "minimal",
        _from_field_not_user,
        f"{P}.performer.from_field",
        "not a user-type field",
    ),
    (
        "form-row-unknown-field",
        "minimal",
        _form_unknown_field,
        "forms.qualify.sections[0].rows[0][0].field",
        "unknown field 'rent'",
    ),
    (
        "show-not-filled-earlier",
        "minimal",
        _show_not_earlier,
        f"{P}.show[0]",
        "not filled by an earlier step",
    ),
    ("bad-field-key", "minimal", _bad_field_key, "fields.SiteCode", "must match"),
    (
        "duplicate-option-id",
        "minimal",
        _duplicate_option,
        "fields.store_model.options",
        "duplicate option id 'cafe'",
    ),
    (
        "select-without-options",
        "minimal",
        _select_without_options,
        "fields.store_model.options",
        "needs options",
    ),
    ("levels-not-contiguous", "minimal", _levels_gap, "org.departments[0].levels", "without gaps"),
    (
        "invalid-group-id",
        "minimal",
        _bad_group_id,
        "org.departments[1].key",
        "not allowed by Operaton",
    ),
    (
        "case-page-without-flow",
        "minimal",
        _case_page_without_flow,
        "apps.pages[0].flow",
        "layout 'case'",
    ),
    ("nav-unknown-page", "minimal", _nav_unknown_page, "apps.nav[0].page", "unknown page 'sites'"),
    (
        "send-back-to-unknown-task",
        "minimal",
        _send_back_unknown,
        f"{P}.approval.send_back_to",
        "is not a task in stage",
    ),
    ("wrong-field-type", "minimal", _wrong_field_type, "fields.site_name.type", "Input should be"),
]


def main() -> None:
    out = HERE / "broken"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    for n, (name, base, mutate, path, message) in enumerate(CASES, start=1):
        doc = copy.deepcopy(load(base))
        mutate(doc)
        errors = validate(doc)
        got = [(e.path, e.message) for e in errors]
        assert len(errors) == 1 and errors[0].path == path and message in errors[0].message, (
            f"{name}: expected one error at {path} containing {message!r}, got {got}"
        )
        sample = {"expected_path": path, "expected_message": message, "document": doc}
        (out / f"{n:02d}-{name}.json").write_text(json.dumps(sample, indent=2) + "\n")
    print(f"wrote {len(CASES)} samples to {out}")


if __name__ == "__main__":
    main()
