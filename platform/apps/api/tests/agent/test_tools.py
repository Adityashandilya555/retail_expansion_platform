"""Agent tools v1 (#36): one row per tool through `tools.call`, with the vault's draft row and
change log held in memory; bad arguments of every kind; the strict schemas. The same rows then run
against the pilot vault's real log (`pytest -m integration` after dev-up.sh)."""

import asyncio
import copy
import hashlib
import json
import random
import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from zm_api import drafts, releases
from zm_api.agent import tools
from zm_api.templates import load_templates
from zm_schema.models import FieldType

from ..test_draft import CHANGES, canonical, pilot  # noqa: F401 (pilot: the vault fixture)
from ..test_publish import Clients, Stack, clients, vault_sql  # noqa: F401
from ..test_publish_unit import matrix_retail

Doc = dict[str, Any]
OWNER = uuid.uuid4()
ZM_FORM = Path(__file__).resolve().parents[4] / "packages" / "zm-forms" / "src" / "ZmForm.tsx"
READ = "<draft_sha256 of the last read>"
"""Stands for the draft's sha256 in an edit's arguments; `agent` puts the one it last got there."""


def stage(doc: Doc, key: str) -> Doc:
    return next(s for s in doc["flows"][0]["stages"] if s["key"] == key)


def ids(doc: Doc, key: str) -> list[Any]:
    """A stage's task ids in order, a parallel group as a list."""
    return [
        [t["id"] for t in i["parallel"]] if "parallel" in i else i["id"]
        for i in stage(doc, key)["tasks"]
    ]


def task(doc: Doc, task_id: str) -> Doc:
    return next(t for _, t in tools._tasks(doc) if t["id"] == task_id)


def dept(doc: Doc, key: str) -> Doc:
    return next(d for d in doc["org"]["departments"] if d["key"] == key)


def qsr() -> Doc:
    return next(t.workflow for t in load_templates() if t.key == "qsr")


def sent(tool: str, args: Doc) -> Doc:
    """`args` as Claude sends them: an edit also names the draft it read."""
    return {**args, "draft_sha256": READ} if tools.TOOLS[tool].edit else args


MANAGER = {"department": "management", "level": 1, "or_above": False}
SLOT = {"accept": ["pdf"], "max_mb": 25, "required": True, "keep_versions": True}

# (tool, arguments as Claude sends them, where to look in the draft after it, what is there)
EDITS: list[tuple[str, Doc, Callable[[Doc], Any], Any]] = [
    (
        "add_department",
        {"key": "ops", "name": "Operations", "levels": ["Ops Executive", "Ops Head"]},
        lambda d: dept(d, "ops")["levels"],
        [{"n": 1, "name": "Ops Executive"}, {"n": 2, "name": "Ops Head"}],
    ),
    (
        "update_department",
        {"department": "legal", "name": "Legal and compliance"},
        lambda d: dept(d, "legal")["name"],
        "Legal and compliance",
    ),
    (
        "set_levels",
        {"department": "legal", "levels": ["Legal Executive", "Legal Supervisor", "Legal Head"]},
        lambda d: dept(d, "legal")["levels"][2],
        {"n": 3, "name": "Legal Head"},
    ),
    (
        "add_stage",
        {
            "flow": "store_launch",
            "key": "handover",
            "name": "Handover",
            "department": "management",
            "after": ["closure"],
            "tasks": [
                {
                    "id": "handover_accept",
                    "name": "Accept the store",
                    "performer": MANAGER,
                    "form": None,
                    "show": [],
                }
            ],
        },
        lambda d: (stage(d, "handover")["after"], stage(d, "handover")["tasks"][0]["performer"]),
        (["closure"], MANAGER),
    ),
    (
        "set_after",
        {"flow": "store_launch", "stage": "launch", "after": ["nso", "project"]},
        lambda d: stage(d, "launch")["after"],
        ["nso", "project"],
    ),
    (
        "add_task",
        {
            "flow": "store_launch",
            "stage": "bd_loi",
            "task": {
                "id": "bd_check_loi",
                "name": "Check the LOI",
                "performer": {"initiator": True},
                "form": None,
                "show": ["monthly_rent"],
            },
            "before": "bd_send_legal",
        },
        lambda d: ids(d, "bd_loi"),
        ["bd_upload_loi", "bd_check_loi", "bd_send_legal"],
    ),
    (
        "update_task",  # every value is set: a new name, the rest as get_task showed them
        {
            "task_id": "bd_review_draft",
            "name": "Review the draft site",
            "performer": {"department": "bd", "level": 2, "or_above": False},
            "form": None,
            "show": [],
        },
        lambda d: (task(d, "bd_review_draft")["name"], "outcomes" in task(d, "bd_review_draft")),
        ("Review the draft site", True),
    ),
    (
        "move_task",
        {"task_id": "prj_push_nso", "flow": "store_launch", "stage": "nso", "before": "nso_comms"},
        lambda d: ("prj_push_nso" in ids(d, "project"), ids(d, "nso")[:2]),
        (False, ["prj_push_nso", "nso_comms"]),
    ),
    (
        "remove_task",  # out of a parallel group of two: the group becomes its other task
        {"task_id": "nso_project_signoff"},
        lambda d: ids(d, "nso"),
        ["nso_comms", "nso_readiness", "nso_signoff", "nso_launch_ready"],
    ),
    (
        "set_outcomes",
        {
            "task_id": "nso_launch_ready",
            "outcomes": [
                {"id": "ready", "label": "Ready", "to": None, "comment_required": False},
                {"id": "redo", "label": "Not ready", "to": "nso_comms", "comment_required": True},
            ],
        },
        lambda d: task(d, "nso_launch_ready")["outcomes"],
        [
            {"id": "ready", "label": "Ready", "comment_required": False},
            {"id": "redo", "label": "Not ready", "to": "nso_comms", "comment_required": True},
        ],
    ),
    (
        "set_approval_chain",
        {
            "task_id": "fc_send",
            "chain": [{"department": "management", "level": 1}],
            "send_back_to": None,
            "allow_reject": False,
        },
        lambda d: task(d, "fc_send")["approval"],
        {"chain": [{"department": "management", "level": 1}], "allow_reject": False},
    ),
    (
        "add_document_slot",
        {"task_id": "legal_licensing", "key": "fire_noc", "label": "Fire NOC upload", **SLOT},
        lambda d: (
            d["fields"]["fire_noc"],
            d["forms"]["legal_licensing"]["sections"][-1]["rows"][-1],
        ),
        (
            {
                "type": "document",
                "label": "Fire NOC upload",
                "required": True,
                "document": {"accept": ["pdf"], "max_mb": 25, "keep_versions": True},
            },
            [{"field": "fire_noc"}],
        ),
    ),
    (
        "add_field",
        {"key": "gst_no", "type": "text", "label": "GST no", "required": False, "options": []},
        lambda d: d["fields"]["gst_no"],
        {"type": "text", "label": "GST no", "required": False},
    ),
    (
        "update_field",
        {
            "key": "site_name",
            "label": "Site name (as on the lease)",
            "options": [],
            "required": False,
        },
        lambda d: d["fields"]["site_name"],
        {"type": "text", "label": "Site name (as on the lease)", "required": False},
    ),
    (
        "remove_field",  # hidden from new forms, its definition (and stored values) kept
        {"key": "frontage_ft"},
        lambda d: d["fields"]["frontage_ft"],
        {"type": "number", "label": "Frontage (ft)", "required": True, "hidden": True},
    ),
    (
        "arrange_form",
        {
            "form": "bd_upload_loi",
            "sections": [
                {
                    "key": "details",
                    "title": "Upload LOI",
                    "rows": [
                        [{"field": "loi_signed_on", "span": 6}, {"field": "loi_file", "span": 6}]
                    ],
                    "hidden_below_level": None,
                }
            ],
        },
        lambda d: d["forms"]["bd_upload_loi"]["sections"][0]["rows"],
        [[{"field": "loi_signed_on", "span": 6}, {"field": "loi_file", "span": 6}]],
    ),
    ("load_template", {"template": "qsr"}, lambda d: d == qsr(), True),  # the draft is untouched
]

# (tool, arguments, where to look in the result, what is there)
READS: list[tuple[str, Doc, Callable[[Doc], Any], Any]] = [
    (
        "get_overview",
        {},
        lambda r: (r["data"]["flows"][0]["stages"][1], r["data"]["fields"]["frontage_ft"]),
        (
            {
                "key": "bd_loi",
                "name": "BD - LOI",
                "department": "bd",
                "after": ["bd_qualification"],
                "tasks": [
                    {"id": "bd_upload_loi", "name": "Upload LOI"},
                    {"id": "bd_send_legal", "name": "Send to Legal and Finance"},
                ],
            },
            {"type": "number", "label": "Frontage (ft)"},
        ),
    ),
    ("get_task", {"task_id": "bd_upload_loi"}, lambda r: r["data"]["show"], ["monthly_rent"]),
    (
        "get_form",
        {"form": "bd_upload_loi"},
        lambda r: (r["data"]["form"]["sections"][0]["key"], sorted(r["data"]["fields"])),
        ("details", ["loi_file", "loi_signed_on"]),
    ),
    (
        "list_field_types",
        {},
        lambda r: [(t["type"], t["v1_renders"]) for t in r["data"]],
        [(t, t in tools.V1_RENDERS) for t in get_args(FieldType)],
    ),
    ("list_templates", {}, lambda r: [t["key"] for t in r["data"]], ["matrix-retail", "qsr"]),
    ("validate_draft", {}, lambda r: (r["ok"], "errors" in r), (True, False)),
    ("diff_vs_live", {}, lambda r: "site_name" in r["data"]["fields"]["added"], True),
]

ARGS = {tool: sent(tool, args) for tool, args, *_ in EDITS + READS}

# Arguments the schema takes but the draft doesn't: each comes back ok=false, changing nothing.
BAD: Doc = {
    tool: sent(tool, args)
    for tool, args in {
        "get_task": {"task_id": "nope"},
        "get_form": {"form": "nope"},
        "load_template": {"template": "nope"},
        "add_department": {"key": "legal", "name": "Legal again", "levels": ["Clerk"]},
        "update_department": {"department": "nope", "name": "Nope"},
        "set_levels": {"department": "bd", "levels": []},
        "add_stage": {**ARGS["add_stage"], "after": ["nope"]},
        "set_after": {"flow": "store_launch", "stage": "launch", "after": ["nso", "nope"]},
        "add_task": {
            **ARGS["add_task"],
            "task": {**ARGS["add_task"]["task"], "id": "bd_review_draft"},
        },
        "update_task": {**ARGS["update_task"], "task_id": "nope"},
        "move_task": {**ARGS["move_task"], "stage": "nope"},
        "remove_task": {"task_id": "bd_upload_loi"},  # bd_send_legal sends back to it
        "set_outcomes": {
            "task_id": "bd_send_legal",
            "outcomes": [{"id": "x", "label": "X", "to": "nope", "comment_required": False}],
        },
        "set_approval_chain": {
            **ARGS["set_approval_chain"],
            "chain": [{"department": "nope", "level": 1}],
        },
        "add_document_slot": {**ARGS["add_document_slot"], "task_id": "bd_review_draft"},
        "add_field": {**ARGS["add_field"], "key": "site_name"},  # taken
        "update_field": {**ARGS["update_field"], "key": "nope"},
        "remove_field": {"key": "nope"},
        "arrange_form": {
            "form": "bd_upload_loi",
            "sections": [
                {
                    "key": "x",
                    "title": "X",
                    "rows": [[{"field": "nope", "span": 12}]],
                    "hidden_below_level": None,
                }
            ],
        },
    }.items()
}


def bad_calls() -> Iterator[tuple[str, Any]]:
    """Every tool with each argument missing, each of the wrong type, an extra one (a client id),
    and its BAD row; plus a tool that doesn't exist."""
    for tool, args in ARGS.items():
        for key in args:
            yield tool, {k: v for k, v in args.items() if k != key}
            yield tool, {**args, key: 12345}
        yield tool, {**args, "client_id": str(uuid.uuid4())}
        yield tool, None
        if tool in BAD:
            yield tool, BAD[tool]
    yield "publish", {}


def nodes(value: Any) -> Iterator[tuple[Any, Any]]:
    """(container, key or index) of every value inside `value`."""
    if isinstance(value, dict | list):
        for k, v in value.items() if isinstance(value, dict) else enumerate(value):
            yield value, k
            yield from nodes(v)


def malformed(rng: random.Random, args: Doc) -> Doc:
    """`args` with one to three random faults, each of which a tool's schema refuses wherever it
    lands: a property dropped (all are required) or added (none is allowed), or a value swapped
    for a fraction, a list of one or an object, which no property takes in its place."""
    args = copy.deepcopy(args)
    for _ in range(rng.randint(1, 3)):
        inside = list(nodes(args))
        objects = [args, *(c[k] for c, k in inside if isinstance(c[k], dict))]
        target, fault = rng.choice(objects), rng.choice(["drop", "add", "swap"])
        if fault == "drop" and (own := [k for k in target if not k.startswith("zz")]):
            del target[rng.choice(own)]  # never an added one: that would undo a fault
        elif fault == "swap" and inside:
            c, k = rng.choice(inside)
            fraction = 0.25 + rng.random() / 2
            c[k] = rng.choice([fraction, [fraction], {"zz": fraction}])
        else:
            target[f"zz_{rng.randrange(100)}"] = rng.random()
    return args


async def agent(ctx: Any, author: uuid.UUID, calls: Any) -> list[Doc]:
    """Runs `calls` the way the agent does: it reads the draft first, and an edit's READ becomes
    the `draft_sha256` its last read or edit returned."""
    # a cut read gives no sha (the agent hasn't seen the whole draft): its edits then name none
    sha = (await tools.call(ctx, author, "get_overview", {})).get("draft_sha256")
    results = []
    for tool, args in calls:
        if isinstance(args, dict) and args.get("draft_sha256") == READ:
            args = {**args, "draft_sha256": sha}
        result = await tools.call(ctx, author, tool, args)
        sha = result.get("draft_sha256", sha)
        results.append(result)
    return results


# --- unit: the vault's draft row and log in memory ----------------------------------------------


def sha(doc: Doc) -> str:
    """The test's stand-in for the vault's sha256 over jsonb's text."""
    return hashlib.sha256(canonical(doc).encode()).hexdigest()


class FakeDb:
    """The vault engine: transactions that hold nothing (the draft's reads and writes are
    patched in `held`)."""

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield None

    connect = begin


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """`drafts.apply_change` runs as it is, on a draft and log kept here."""
    state = SimpleNamespace(doc=matrix_retail(), log=[], ctx=SimpleNamespace(db=FakeDb()))

    async def draft(_conn: Any, *, lock: bool = False) -> Doc:
        return state.doc

    async def read(_conn: Any) -> tuple[Doc, str]:
        return state.doc, sha(state.doc)

    async def log(_conn: Any, after: Doc, change: Doc) -> SimpleNamespace:
        state.doc = after
        state.log.append(change)
        return SimpleNamespace(**change, after_sha256=sha(after))

    async def none(_conn: Any) -> None:
        return None

    monkeypatch.setattr(drafts, "_draft", draft)
    monkeypatch.setattr(drafts, "read", read)
    monkeypatch.setattr(drafts, "_log", log)
    monkeypatch.setattr(releases, "active_release", none)
    return state


def call(state: SimpleNamespace, tool: str, args: Any) -> Doc:
    """One call after a read, as the agent makes it."""
    [result] = asyncio.run(agent(state.ctx, OWNER, [(tool, args)]))
    return result


@pytest.mark.parametrize(("tool", "args", "look", "want"), EDITS, ids=[e[0] for e in EDITS])
def test_each_edit_applies_its_patch_and_logs_its_tool(
    held: SimpleNamespace, tool: str, args: Doc, look: Callable[[Doc], Any], want: Any
) -> None:
    result = call(held, tool, sent(tool, args))
    assert result["ok"], result
    assert look(held.doc) == want
    assert [(c["tool"], c["summary"], c["author_owner_id"]) for c in held.log] == [
        (tool, result["summary"], OWNER)
    ]
    assert result["draft_sha256"] == sha(held.doc)


@pytest.mark.parametrize(("tool", "args", "look", "want"), READS, ids=[r[0] for r in READS])
def test_each_read_answers_from_the_draft_and_changes_nothing(
    held: SimpleNamespace, tool: str, args: Doc, look: Callable[[Doc], Any], want: Any
) -> None:
    result = call(held, tool, args)
    assert look(result) == want
    assert held.doc == matrix_retail() and held.log == []


def edits() -> list[tuple[str, Doc]]:
    """Every edit but load_template (an untouched draft only), in the table's order."""
    return [(tool, sent(tool, args)) for tool, args, *_ in EDITS if tool != "load_template"]


def fold() -> Doc:
    """The Matrix Retail draft after `edits()`, worked out from the patch builders alone."""
    doc = matrix_retail()
    for tool, args in edits():
        t = tools.TOOLS[tool]
        assert t.edit is not None
        doc, _ = drafts.patched(doc, t.edit(doc, t.args.model_validate(args)))
    return doc


def test_the_edits_add_up_one_after_another(held: SimpleNamespace) -> None:
    assert all(r["ok"] for r in asyncio.run(agent(held.ctx, OWNER, edits())))
    assert [c["tool"] for c in held.log] == [tool for tool, _ in edits()]
    assert canonical(held.doc) == canonical(fold())


def test_bad_arguments_come_back_as_errors_and_change_nothing(held: SimpleNamespace) -> None:
    for tool, args in bad_calls():
        result = call(held, tool, args)
        assert result["ok"] is False and result["summary"], (tool, args, result)
    assert held.doc == matrix_retail() and held.log == []


def test_random_malformed_arguments_come_back_as_errors_and_change_nothing(
    held: SimpleNamespace,
) -> None:
    rng = random.Random(36)  # seeded: a failing case names itself and comes back on a rerun
    for tool, args in ARGS.items():
        for _ in range(20):
            bad = malformed(rng, args)
            result = call(held, tool, bad)
            assert result["ok"] is False and result["summary"], (tool, bad, result)
    assert held.doc == matrix_retail() and held.log == []


def test_a_bad_argument_comes_back_with_its_path(held: SimpleNamespace) -> None:
    args = {**ARGS["add_task"], "task": {**ARGS["add_task"]["task"], "performer": {"level": 1}}}
    result = call(held, "add_task", args)
    assert result["ok"] is False
    assert "task.performer.department" in [e["path"] for e in result["errors"]]
    draft = call(held, "set_after", BAD["set_after"])
    assert draft["errors"] == [
        {
            "path": "flows[0].stages[8].after[1]",
            "message": "unknown stage 'nope' in flow 'store_launch'",
        }
    ]


def test_set_outcomes_refuses_one_id_twice(held: SimpleNamespace) -> None:
    # both would compile to the same XOR condition, so the second could never be taken
    outcomes = [
        {"id": "ready", "label": "Ready", "to": None, "comment_required": False},
        {"id": "ready", "label": "Not ready", "to": "nso_comms", "comment_required": True},
    ]
    args = sent("set_outcomes", {"task_id": "nso_launch_ready", "outcomes": outcomes})
    result = call(held, "set_outcomes", args)
    assert result["ok"] is False
    assert [e["message"] for e in result["errors"]] == ["duplicate outcome id 'ready'"]
    assert held.doc == matrix_retail() and held.log == []


def test_update_field_never_changes_a_key(held: SimpleNamespace) -> None:
    renamed = call(held, "update_field", {**ARGS["update_field"], "new_key": "site_title"})
    created = call(held, "update_field", {**ARGS["update_field"], "key": "site_title"})
    assert renamed["ok"] is False and created["ok"] is False
    assert held.doc == matrix_retail() and held.log == []


def test_update_task_sets_every_value_so_a_form_or_shown_fields_can_be_cleared(
    held: SimpleNamespace,
) -> None:
    upload = task(matrix_retail(), "bd_upload_loi")
    args = {**ARGS["update_task"], "task_id": "bd_upload_loi", "name": upload["name"]}
    cleared = {**args, "performer": upload["performer"], "form": None, "show": []}
    patch = tools.update_task(matrix_retail(), tools.TaskChanges.model_validate(cleared))
    at = "/flows/0/stages/1/tasks/0"
    assert [p for p in patch if p["op"] == "remove"] == [
        {"op": "remove", "path": f"{at}/form"},
        {"op": "remove", "path": f"{at}/show"},
    ]
    assert call(held, "update_task", {**cleared, "form": "bd_upload_loi"})["ok"]
    assert task(held.doc, "bd_upload_loi") == {k: v for k, v in upload.items() if k != "show"}


def test_load_template_only_replaces_an_untouched_draft(held: SimpleNamespace) -> None:
    assert call(held, "load_template", sent("load_template", {"template": "matrix-retail"})) == {
        "ok": True,
        "summary": "Nothing changed",
        "draft_sha256": sha(matrix_retail()),
    }
    assert call(held, "add_field", ARGS["add_field"])["ok"]
    refused = call(held, "load_template", sent("load_template", {"template": "qsr"}))
    assert refused["ok"] is False and "unchanged template" in refused["summary"]
    assert [c["tool"] for c in held.log] == ["add_field"]


def test_an_empty_approval_chain_removes_the_approval(held: SimpleNamespace) -> None:
    assert call(held, "set_approval_chain", ARGS["set_approval_chain"])["ok"]
    none = {**ARGS["set_approval_chain"], "chain": []}
    assert call(held, "set_approval_chain", none)["ok"]
    assert "approval" not in task(held.doc, "fc_send")
    assert call(held, "set_approval_chain", none)["summary"] == "Nothing changed"


def test_an_edit_made_on_a_read_older_than_the_draft_is_refused(held: SimpleNamespace) -> None:
    def read() -> str:
        return call(held, "get_task", {"task_id": "bd_upload_loi"})["draft_sha256"]

    def update_field(seen: str) -> Doc:
        return call(held, "update_field", {**ARGS["update_field"], "draft_sha256": seen})

    before = read()
    # a person changes the draft in the console after the agent read it
    asyncio.run(drafts.apply_change(held.ctx, OWNER, "set_theme", CHANGES[2], "Accent: copper"))
    assert update_field(before) == {"ok": False, "summary": drafts.STALE}
    assert [c["tool"] for c in held.log] == ["set_theme"]
    after = read()
    done = update_field(after)
    assert done["ok"] and done["draft_sha256"] == sha(held.doc) != after != before
    assert [c["tool"] for c in held.log] == ["set_theme", "update_field"]


def test_a_long_result_is_cut_and_says_so(
    held: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "truncated" not in call(held, "get_overview", {})  # the Matrix Retail draft fits
    monkeypatch.setattr(tools, "MAX_DATA", 100)
    monkeypatch.setattr(tools, "MAX_ERRORS", 2)
    overview = call(held, "get_overview", {})
    assert overview["truncated"] is True
    assert overview["data"] == json.dumps(tools.overview(matrix_retail()))[:100]
    assert "draft_sha256" not in overview  # no edit may build on a read it couldn't see whole
    assert overview["summary"].endswith("(cut: read the parts with get_task or get_form)")
    empty = call(held, "add_field", sent("add_field", {}))  # five arguments missing
    assert empty["truncated"] is True and len(empty["errors"]) == 2


# Anthropic's structured-output limits, summed over every strict schema in one request
# ("Schema complexity limits"; past any of them the request is a 400):
# https://platform.claude.com/docs/en/build-with-claude/structured-outputs
STRICT_TOOLS, UNION_PARAMETERS, OPTIONAL_PARAMETERS = 20, 16, 24


def schemas(node: Any) -> Iterator[Doc]:
    """Every object schema inside a JSON Schema, `$defs` included."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from schemas(value)
    elif isinstance(node, list):
        for value in node:
            yield from schemas(value)


def test_the_strict_tools_fit_the_api_limits() -> None:
    strict = [d for d in tools.definitions() if d["strict"]]
    parameters = [  # every property of every object, nested ones and `$defs` included
        (p, name in o.get("required", []))
        for d in strict
        for o in schemas(d["input_schema"])
        for name, p in o.get("properties", {}).items()
    ]
    unions = [p for p, _ in parameters if "anyOf" in p or isinstance(p.get("type"), list)]
    optional = [p for p, required in parameters if not required]
    assert len(strict) <= STRICT_TOOLS, len(strict)
    assert len(unions) <= UNION_PARAMETERS, len(unions)
    assert len(optional) <= OPTIONAL_PARAMETERS, len(optional)


def test_every_tool_schema_is_strict_and_names_no_client_or_tenant() -> None:
    definitions = tools.definitions()
    assert [d["name"] for d in definitions] == list(tools.TOOLS)
    assert sorted(tools.TOOLS) == sorted(ARGS)  # a row per tool, and no tool beyond the spec
    for d in definitions:
        # the edits are strict; reads and checks aren't, to stay inside the limits above
        assert d["strict"] is (tools.TOOLS[d["name"]].edit is not None) and d["description"]
        objects = list(schemas(d["input_schema"]))
        assert objects[0] is d["input_schema"]
        for schema in objects:
            properties = schema.get("properties", {})
            assert schema["additionalProperties"] is False, d["name"]
            assert sorted(schema.get("required", [])) == sorted(properties), d["name"]
            assert not [p for p in properties if "client" in p or "tenant" in p], d["name"]
    # keywords strict tool schemas don't take: the draft's validation checks these instead
    assert not re.search(
        r'"(minLength|maxLength|minimum|maximum|pattern)":', json.dumps(definitions)
    )


def test_v1_renders_exactly_what_zm_forms_draws() -> None:
    drawn = set(re.findall(r'case "(\w+)":', ZM_FORM.read_text()))
    assert drawn == tools.V1_RENDERS
    assert drawn < set(get_args(FieldType))


# --- integration: the pilot vault --------------------------------------------------------------


def run(c: Clients, *calls: tuple[str, Any]) -> list[Doc]:
    async def go() -> list[Doc]:
        async with Stack(c) as stack:
            return await agent(await stack.ctx("pilot"), c.owner, calls)

    return asyncio.run(go())


@pytest.mark.integration
def test_every_edit_lands_in_the_vault_logged_with_its_tool(pilot: Clients) -> None:  # noqa: F811
    results = run(pilot, *edits())
    assert all(r["ok"] for r in results), results
    log = vault_sql(
        "pilot", "select tool, summary, after_sha256 from app.draft_changes order by id"
    )
    [(draft,)] = vault_sql("pilot", "select workflow from app.drafts")
    assert log == [
        (tool, r["summary"], r["draft_sha256"])
        for (tool, _), r in zip(edits(), results, strict=True)
    ]
    assert canonical(draft) == canonical(fold())


@pytest.mark.integration
def test_bad_arguments_leave_the_vault_draft_as_it_was(pilot: Clients) -> None:  # noqa: F811
    [first] = run(pilot, ("add_field", ARGS["add_field"]))
    results = run(pilot, *bad_calls())
    assert first["ok"] and not [r for r in results if r["ok"]]
    [(logged,)] = vault_sql("pilot", "select after_sha256 from app.draft_changes")  # still one
    [(now,)] = vault_sql("pilot", f"select {drafts._DRAFT_SHA}")
    assert now == logged


@pytest.mark.integration
def test_reads_answer_from_the_vault(pilot: Clients) -> None:  # noqa: F811
    results = run(pilot, *[(tool, args) for tool, args, *_ in READS])
    for (tool, _, look, want), result in zip(READS, results, strict=True):
        assert look(result) == want, tool
    assert vault_sql("pilot", "select count(*) from app.draft_changes") == [(0,)]
    [(now,)] = vault_sql("pilot", f"select {drafts._DRAFT_SHA}")
    assert results[0]["draft_sha256"] == now


@pytest.mark.integration
def test_an_edit_on_a_stale_read_is_refused_in_the_vault(pilot: Clients) -> None:  # noqa: F811
    async def go() -> tuple[Doc, Doc]:
        async with Stack(pilot) as stack:
            ctx = await stack.ctx("pilot")
            read = await tools.call(ctx, pilot.owner, "get_task", {"task_id": "bd_upload_loi"})
            await drafts.apply_change(ctx, pilot.owner, "set_theme", CHANGES[2], "Accent: copper")
            stale = {**ARGS["update_field"], "draft_sha256": read["draft_sha256"]}
            return read, await tools.call(ctx, pilot.owner, "update_field", stale)

    read, refused = asyncio.run(go())
    assert refused == {"ok": False, "summary": drafts.STALE}
    [(tool, logged)] = vault_sql("pilot", "select tool, after_sha256 from app.draft_changes")
    assert tool == "set_theme" and logged != read["draft_sha256"]


@pytest.mark.integration
def test_load_template_replaces_the_seeded_draft_but_not_one_with_work_in_it(
    pilot: Clients,  # noqa: F811
) -> None:
    loaded, added, refused = run(
        pilot,
        # the draft round-tripped jsonb: still the template
        ("load_template", sent("load_template", {"template": "qsr"})),
        ("add_field", ARGS["add_field"]),
        ("load_template", sent("load_template", {"template": "matrix-retail"})),
    )
    assert loaded["ok"] and added["ok"] and not refused["ok"]
    assert vault_sql("pilot", "select tool from app.draft_changes order by id") == [
        ("load_template",),
        ("add_field",),
    ]
