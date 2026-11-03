"""Pages against the UI packages (#37): `validate()` checks each page of `apps` against
`packages/zm-blocks/catalogue.json` and names the page and the region."""

from typing import Any

from zm_schema import validate


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
        "apps.pages[0].regions.main[0].block: page 'home': region 'main' doesn't take block"
        " 'case_header'",
        "apps.pages[0].regions.main[1].block: page 'home': region 'main' doesn't take block"
        " 'kpi_tile'",
        "apps.pages[0].regions.hero: page 'home': region 'hero' holds at most 1",
        "apps.pages[0].regions.hero[0].block: page 'home': region 'hero' doesn't take block"
        " 'task_inbox'",
        "apps.pages[0].regions.hero[1].block: page 'home': region 'hero' doesn't take block"
        " 'task_inbox'",
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
