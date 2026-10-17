import json
from pathlib import Path

import pytest

from zm_schema import cli

TESTS = Path(__file__).parent


def test_validate_valid_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["validate", str(TESTS / "valid" / "two_flows.json")]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_broken_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sample = json.loads((TESTS / "broken" / "11-show-not-filled-earlier.json").read_text())
    doc = tmp_path / "workflow.json"
    doc.write_text(json.dumps(sample["document"]))
    assert cli.main(["validate", str(doc)]) == 1
    assert sample["expected_path"] in capsys.readouterr().out


def test_validate_unreadable_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert cli.main(["validate", str(bad)]) == 2


def test_committed_schema_is_up_to_date() -> None:
    assert cli.main(["export", "--check"]) == 0


def test_export_check_detects_stale_file(tmp_path: Path) -> None:
    stale = tmp_path / "workflow.schema.json"
    stale.write_text("{}\n")
    assert cli.main(["export", "--check", "--out", str(stale)]) == 1
    assert cli.main(["export", "--out", str(stale)]) == 0
    assert cli.main(["export", "--check", "--out", str(stale)]) == 0
    assert json.loads(stale.read_text())["title"] == "WorkflowV1"
