"""`zm-compat` (#31): a config that would no longer publish or draw fails, naming where."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from zm_compiler import compat

TEMPLATES = compat.TEMPLATES_DIR


def template(key: str) -> dict[str, Any]:
    return json.loads((TEMPLATES / key / "workflow.json").read_text())


def packages(**changes: Any) -> compat.Packages:
    real = compat.Packages.load()
    return compat.Packages(
        layouts=changes.get("layouts", copy.deepcopy(real.layouts)),
        blocks=changes.get("blocks", copy.deepcopy(real.blocks)),
        planned=changes.get("planned", dict(real.planned)),
    )


def messages(problems: list[compat.Problem]) -> list[str]:
    return [str(p) for p in problems]


def test_everything_we_ship_passes_today(capsys: pytest.CaptureFixture[str]) -> None:
    assert compat.main(["--fixtures"]) == 0
    out = capsys.readouterr().out
    assert "4 configs compile and draw" in out
    assert "'task_inbox' is placed" in out and "planned in #30" in out


def test_a_removed_layout_names_the_template_and_page() -> None:
    real = packages()
    del real.layouts["case"]
    found = messages(
        compat.check_document("template matrix-retail", template("matrix-retail"), real)
    )
    assert found == ["template matrix-retail · page site: layout 'case' has no manifest"]


def test_a_removed_block_names_the_template_and_page() -> None:
    real = packages()
    del real.planned["case_header"]  # neither built nor planned any more
    found = messages(compat.check_document("template qsr", template("qsr"), real))
    assert found == [
        "template qsr · page store: block 'case_header' (region header) has no manifest and"
        " isn't planned"
    ]


def test_an_unknown_region_and_a_full_region() -> None:
    doc = template("qsr")
    home = next(p for p in doc["apps"]["pages"] if p["key"] == "home")
    home["regions"]["sidebar"] = [{"block": "task_inbox"}]
    home["regions"]["hero"] = [{"block": "task_inbox"}, {"block": "case_table"}]
    found = messages(compat.check_document("template qsr", doc, packages()))
    assert "template qsr · page home: layout 'standard' has no region 'sidebar'" in found
    assert "template qsr · page home: region 'hero' holds at most 1" in found


def test_a_layout_requirement_and_allowed_blocks() -> None:
    real = packages()
    real.layouts["standard"]["requires"] = ["department"]
    real.layouts["standard"]["regions"][1]["allowed_blocks"] = ["case_table"]
    ui = {
        "pages": [
            {"key": "home", "layout": "standard", "regions": {"main": [{"block": "task_inbox"}]}}
        ]
    }
    found = messages(compat.check_pages("fixture x", ui, real))
    assert found == [
        "fixture x · page home: layout 'standard' needs department",
        "fixture x · page home: region 'main' doesn't take block 'task_inbox'",
    ]


def test_a_built_block_is_checked_against_its_manifest() -> None:
    manifest = {
        "name": "task_inbox",
        "version": 1,
        "description": "x",
        "regions": ["main"],
        "props": {
            "type": "object",
            "properties": {"scope": {"enum": ["mine", "team"]}},
            "additionalProperties": False,
        },
    }
    real = packages(blocks={"task_inbox": manifest})
    del real.planned["task_inbox"]
    ui = {
        "pages": [
            {
                "key": "home",
                "layout": "standard",
                "regions": {
                    "main": [{"block": "task_inbox", "props": {"scope": "everyone"}}],
                    "aside": [{"block": "task_inbox"}],
                },
            }
        ]
    }
    found = messages(compat.check_pages("template x", ui, real))
    assert found == [
        "template x · page home: block 'task_inbox' prop scope: 'everyone' is not one of"
        " ['mine', 'team']",
        "template x · page home: block 'task_inbox' can't go in region 'aside'",
    ]


def test_a_planned_block_with_a_manifest_is_stale() -> None:
    real = packages(blocks={"task_inbox": {"name": "task_inbox", "regions": ["main"]}})
    assert messages(compat.check_packages(real)) == [
        "planned.json · task_inbox: has a manifest now: remove it from planned.json"
    ]


def test_a_document_that_no_longer_compiles_fails() -> None:
    doc = template("qsr")
    doc["flows"][0]["stages"][0]["department"] = "nowhere"
    found = messages(compat.check_document("fixture broken", doc, packages()))
    assert len(found) == 1 and found[0].startswith("fixture broken · document: doesn't compile")


def test_main_exits_1_and_says_what_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocks = tmp_path / "zm-blocks"
    (blocks / "layouts/standard").mkdir(parents=True)  # no `case` layout any more
    real = compat.BLOCKS_DIR / "layouts/standard/manifest.json"
    (blocks / "layouts/standard/manifest.json").write_text(real.read_text())
    (blocks / "planned.json").write_text((compat.BLOCKS_DIR / "planned.json").read_text())
    monkeypatch.setattr(compat, "BLOCKS_DIR", blocks)
    assert compat.main([]) == 1
    err = capsys.readouterr().err
    assert "FAIL template matrix-retail · page site: layout 'case' has no manifest" in err
