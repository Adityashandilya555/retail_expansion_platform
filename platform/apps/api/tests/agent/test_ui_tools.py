"""The agent's UI tools (#39): pages, blocks and the menu, checked against the layout and block
manifests (catalogue.json, #37) when the draft validates. test_tools runs a row per tool; these
are the acceptance criteria and the refusals, on the Matrix Retail draft held in memory."""

import json
import re
from types import SimpleNamespace

import pytest

from zm_api.agent import tools

from ..test_publish_unit import matrix_retail
from .test_tools import ARGS, Doc, call, held, page, sent  # noqa: F401 (held: the fixture)

INBOX = {
    "page": "home",
    "region": "main",
    "block": "task_inbox",
    "props": {"scope": "team"},
    "index": None,
}
NEW_PAGE = {"key": "store", "layout": "standard", "department": None, "flow": None}

# (tool, arguments, the problem it is refused with as `path: message`; None = done)
CASES: list[tuple[str, Doc, str | None]] = [
    ("place_block", INBOX, None),
    (
        "place_block",
        {**INBOX, "props": {"scope": "all"}},
        "apps.pages[0].regions.main[1].props.scope: page 'home', region 'main': block"
        " 'task_inbox': 'all' is not one of ['mine', 'team']",
    ),
    (
        "place_block",
        {**INBOX, "region": "sidebar"},
        "apps.pages[0].regions.sidebar: page 'home': layout 'standard' has no region 'sidebar'",
    ),
    (
        "place_block",
        {**INBOX, "region": "aside", "block": "case_table", "props": {}},
        "apps.pages[0].regions.aside[0].block: page 'home': region 'aside' of layout 'standard'"
        " doesn't take block 'case_table'",
    ),
    (
        "place_block",
        {**INBOX, "block": "kpi_tiles", "props": {}},
        "apps.pages[0].regions.main[1].block: page 'home': unknown block 'kpi_tiles'",
    ),
    ("place_block", {**INBOX, "index": 2}, "No position 2 in region 'main' of page 'home'"),
    ("place_block", {**INBOX, "page": "nope"}, "No page 'nope'"),
    (
        "update_block",
        {"page": "home", "region": "main", "index": 0, "props": {"scope": "all"}},
        "apps.pages[0].regions.main[0].props.scope: page 'home', region 'main': block"
        " 'task_inbox': 'all' is not one of ['mine', 'team']",
    ),
    (
        "update_block",
        {"page": "home", "region": "aside", "index": 0, "props": {}},
        "No block 0 in region 'aside' of page 'home'",
    ),
    (
        "remove_block",
        {"page": "home", "region": "main", "index": -1},
        "No block -1 in region 'main' of page 'home'",
    ),
    ("set_page", NEW_PAGE, None),
    (
        "set_page",
        {**NEW_PAGE, "layout": "case"},
        "apps.pages[10].flow: page 'store': layout 'case' needs flow",
    ),
    (
        "set_page",
        {**NEW_PAGE, "layout": "grid"},
        "apps.pages[10].layout: page 'store': unknown layout 'grid'",
    ),
    (
        "set_page",  # the page's blocks stay, and `case` has no `main`
        {**NEW_PAGE, "key": "home", "layout": "case", "flow": "store_launch"},
        "apps.pages[0].regions.main: page 'home': layout 'case' has no region 'main'",
    ),
    (
        "set_nav",
        {"items": [{"page": "nope", "label": "Nope", "visible_to": ["*"]}]},
        "apps.nav[0].page: unknown page 'nope'",
    ),
]


def problems(result: Doc) -> list[str]:
    """A refusal's problems as `path: message`, or its summary when it lists none."""
    return [f"{e['path']}: {e['message']}" for e in result.get("errors", [])] or [result["summary"]]


@pytest.mark.parametrize(("tool", "args", "problem"), CASES)
def test_a_ui_edit_is_checked_against_the_manifests(
    held: SimpleNamespace,  # noqa: F811
    tool: str,
    args: Doc,
    problem: str | None,
) -> None:
    result = call(held, tool, sent(tool, args))
    if problem is None:
        assert result["ok"], result
        assert [c["tool"] for c in held.log] == [tool]
    else:  # refused, and the draft is as it was
        assert result["ok"] is False and problem in problems(result), result
        assert held.doc == matrix_retail() and held.log == []


def test_set_page_changes_a_page_and_keeps_its_blocks(held: SimpleNamespace) -> None:  # noqa: F811
    args = {"key": "bd", "layout": "standard", "department": None, "flow": "store_launch"}
    assert call(held, "set_page", sent("set_page", args))["ok"]
    bd = page(matrix_retail(), "bd")
    assert bd["department"] == "bd" and "flow" not in bd
    assert page(held.doc, "bd") == {
        "key": "bd",
        "layout": "standard",
        "flow": "store_launch",
        "regions": bd["regions"],
    }


def test_place_block_puts_it_at_its_index(held: SimpleNamespace) -> None:  # noqa: F811
    assert call(held, "place_block", sent("place_block", {**INBOX, "index": 0}))["ok"]
    assert [b["props"]["scope"] for b in page(held.doc, "home")["regions"]["main"]] == [
        "team",
        "mine",
    ]


def test_a_cut_overview_still_names_the_pages_and_get_page_reads_one_whole(
    held: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = matrix_retail()["apps"]
    ui = {  # each page without its blocks, and the menu
        "pages": [{k: v for k, v in p.items() if k != "regions"} for p in apps["pages"]],
        "nav": apps["nav"],
    }
    monkeypatch.setattr(tools, "MAX_DATA", len(json.dumps(ui)))  # the overview no longer fits
    overview = call(held, "get_overview", {})
    assert overview["truncated"] is True and "draft_sha256" not in overview
    assert overview["data"].startswith(json.dumps(ui)[:-1])  # the cut keeps them whole
    read = call(held, "get_page", {"page": "management"})
    assert read["data"] == page(matrix_retail(), "management") and "truncated" not in read
    remove = {**ARGS["remove_block"], "draft_sha256": read["draft_sha256"]}
    assert call(held, "remove_block", remove)["ok"]


def test_no_tool_sets_the_theme_or_accent() -> None:
    # D19: the client's theme preset and default accent are picked in the owner console
    schemas = {d["name"]: json.dumps(d["input_schema"]) for d in tools.definitions()}
    assert not [n for n, s in schemas.items() if re.search("theme|accent", n + s)]
