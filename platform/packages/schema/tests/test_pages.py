"""Pages against the UI packages (#37): `validate()` checks each page of `apps` against
`packages/zm-blocks/catalogue.json` and names the page and the region."""

import copy
from typing import Any

import pytest

from zm_schema import catalogue, validate


def errors(doc: dict[str, Any], *pages: dict[str, Any]) -> list[str]:
    doc["apps"] = {"pages": list(pages)}
    return [str(e) for e in validate(doc)]


def test_pages_that_fit_their_layouts_are_valid(minimal: dict[str, Any]) -> None:
    home = {
        "key": "home",
        "layout": "standard",
        "regions": {
            "main": [{"block": "task_inbox", "props": {"scope": "team"}}, {"block": "case_table"}],
            "aside": [{"block": "task_inbox"}],
        },
    }
    site = {
        "key": "site",
        "layout": "case",
        "flow": "site_launch",
        "regions": {
            "header": [{"block": "case_header", "props": {"title_field": "site_name"}}],
            "overview": [{"block": "case_fields"}],
            "tasks": [{"block": "case_tasks"}],
        },
    }
    assert errors(minimal, home, site) == []


def test_a_block_its_region_doesnt_take_and_a_full_region(minimal: dict[str, Any]) -> None:
    home = {
        "key": "home",
        "layout": "standard",
        "regions": {
            "main": [{"block": "case_header"}, {"block": "kpi_tile"}],
            "hero": [{"block": "task_inbox"}, {"block": "task_inbox"}],
        },
    }
    assert errors(minimal, home) == [
        "apps.pages[0].regions.main[0].block: page 'home': region 'main' of layout 'standard'"
        " doesn't take block 'case_header'",
        "apps.pages[0].regions.main[1].block: page 'home': unknown block 'kpi_tile'",
        "apps.pages[0].regions.hero: page 'home': region 'hero' holds at most 1",
        "apps.pages[0].regions.hero[0].block: page 'home': region 'hero' of layout 'standard'"
        " doesn't take block 'task_inbox'",
        "apps.pages[0].regions.hero[1].block: page 'home': region 'hero' of layout 'standard'"
        " doesn't take block 'task_inbox'",
    ]


def test_the_look_is_a_registered_preset_and_accent(minimal: dict[str, Any]) -> None:
    # catalogue.json lists the shell's presets and accents (@zm/ui theme.ts); the shell would only
    # fall back to quartz and the preset's accent for anything else
    minimal["apps"] = {"theme": "warm", "accent": "copper"}
    assert validate(minimal) == []
    minimal["apps"] = {"theme": "neon", "accent": "chartreuse"}
    assert [str(e) for e in validate(minimal)] == [
        "apps.theme: unknown theme preset 'neon'",
        "apps.accent: unknown accent 'chartreuse'",
    ]


def test_a_case_page_needs_a_flow(minimal: dict[str, Any]) -> None:
    site = {"key": "site", "layout": "case", "regions": {"header": [{"block": "case_header"}]}}
    assert errors(minimal, site) == ["apps.pages[0].flow: page 'site': layout 'case' needs flow"]


def test_an_unknown_layout_or_region(minimal: dict[str, Any]) -> None:
    dash = {"key": "dash", "layout": "dashboard"}
    home = {"key": "home", "layout": "standard", "regions": {"sidebar": [{"block": "task_inbox"}]}}
    assert errors(minimal, dash, home) == [
        "apps.pages[0].layout: page 'dash': unknown layout 'dashboard'",
        "apps.pages[1].regions.sidebar: page 'home': layout 'standard' has no region 'sidebar'",
    ]


def test_props_are_checked_against_the_blocks_manifest(minimal: dict[str, Any]) -> None:
    home = {
        "key": "home",
        "layout": "standard",
        "regions": {"main": [{"block": "task_inbox", "props": {"scope": "all", "color": "red"}}]},
    }
    assert errors(minimal, home) == [
        "apps.pages[0].regions.main[0].props: page 'home', region 'main': block 'task_inbox':"
        " Additional properties are not allowed ('color' was unexpected)",
        "apps.pages[0].regions.main[0].props.scope: page 'home', region 'main': block"
        " 'task_inbox': 'all' is not one of ['mine', 'team']",
    ]


def test_props_of_any_registered_version_pass(
    minimal: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # task_inbox v2 renames `scope` to `audience`: a page names no version, so one built on v1
    # stays valid (the shell draws v1 for it); props neither version takes get v2's errors.
    ui = copy.deepcopy(catalogue.load())
    v1 = ui["blocks"]["task_inbox"]["1"]
    props = {**v1["props"], "properties": {"audience": {"enum": ["me"]}}}
    ui["blocks"]["task_inbox"]["2"] = {**v1, "version": 2, "props": props}
    monkeypatch.setattr(catalogue, "load", lambda: ui)
    home = {
        "key": "home",
        "layout": "standard",
        "regions": {
            "main": [
                {"block": "task_inbox", "props": {"scope": "team"}},
                {"block": "task_inbox", "props": {"audience": "me"}},
                {"block": "task_inbox", "props": {"scope": "me"}},
            ]
        },
    }
    assert errors(minimal, home) == [
        "apps.pages[0].regions.main[2].props: page 'home', region 'main': block 'task_inbox':"
        " Additional properties are not allowed ('scope' was unexpected)",
    ]
