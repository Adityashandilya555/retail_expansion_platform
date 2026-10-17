import json
from pathlib import Path

import pytest

from zm_schema import cli

TESTS = Path(__file__).parent


def test_validate_valid_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(TESTS)
    assert cli.main(["validate", "valid/two_flows.json"]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_broken_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = json.loads((TESTS / "broken" / "11-show-not-filled-earlier.json").read_text())
    (tmp_path / "workflow.json").write_text(json.dumps(sample["document"]))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["validate", "workflow.json"]) == 1
    assert sample["expected_path"] in capsys.readouterr().out


def test_validate_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["validate", "bad.json"]) == 2


@pytest.mark.parametrize("name", ["../outside.json", "/etc/hosts", "missing.json"])
def test_validate_refuses_paths_outside_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (tmp_path / "outside.json").write_text("{}")
    inner = tmp_path / "inner"
    inner.mkdir()
    monkeypatch.chdir(inner)
    assert cli.main(["validate", name]) == 2


def test_committed_schema_is_up_to_date() -> None:
    assert cli.main(["export", "--check"]) == 0


def test_export_check_detects_stale_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = tmp_path / "workflow.schema.json"
    monkeypatch.setattr(cli, "SCHEMA_FILE", stale)
    assert cli.main(["export", "--check"]) == 1
    stale.write_text("{}\n")
    assert cli.main(["export", "--check"]) == 1
    assert cli.main(["export"]) == 0
    assert cli.main(["export", "--check"]) == 0
    assert json.loads(stale.read_text())["title"] == "WorkflowV1"
