"""`zm-compat` (#31): a config that would no longer publish or draw fails, naming where."""

import copy
import json
from typing import Any

import pytest

from zm_compiler import compat
from zm_schema import catalogue

TEMPLATES = compat.TEMPLATES_DIR


def template(key: str) -> dict[str, Any]:
    return json.loads((TEMPLATES / key / "workflow.json").read_text())


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A copy of catalogue.json (#37) that validation reads: break it as a PR would."""
    broken = copy.deepcopy(catalogue.load())
    monkeypatch.setattr(catalogue, "load", lambda: broken)
    return broken


def messages(problems: list[compat.Problem]) -> list[str]:
    return [str(p) for p in problems]


def test_everything_we_ship_passes_today(capsys: pytest.CaptureFixture[str]) -> None:
    assert compat.main(["--fixtures"]) == 0
    assert "4 configs compile and draw" in capsys.readouterr().out


def test_a_removed_layout_names_the_template_and_page(ui: dict[str, Any]) -> None:
    del ui["layouts"]["case"]
    found = messages(compat.check_document("template matrix-retail", template("matrix-retail")))
    assert found == [
        "template matrix-retail · apps.pages[9].layout: page 'site': unknown layout 'case'"
    ]


def test_a_removed_block_names_the_template_and_page(ui: dict[str, Any]) -> None:
    del ui["blocks"]["case_header"]
    found = messages(compat.check_document("template qsr", template("qsr")))
    assert found == [
        "template qsr · apps.pages[2].regions.header[0].block: page 'store': unknown block"
        " 'case_header'"
    ]


def test_a_document_that_no_longer_validates_fails() -> None:
    doc = template("qsr")
    doc["flows"][0]["stages"][0]["department"] = "nowhere"
    found = messages(compat.check_document("fixture broken", doc))
    assert found == ["fixture broken · flows[0].stages[0].department: unknown department 'nowhere'"]


def test_main_exits_1_and_says_what_failed(
    ui: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    del ui["layouts"]["case"]
    assert compat.main([]) == 1
    err = capsys.readouterr().err
    assert "FAIL template matrix-retail · apps.pages[9].layout: page 'site'" in err
    assert "zm-compat: 2 problem(s) in 2 configs" in err
