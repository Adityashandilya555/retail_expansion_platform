"""packages/api-client/openapi.json is generated from the app; CI fails when it is stale (#21)."""

import json
from pathlib import Path

import pytest

from zm_api import openapi
from zm_api.cli import main


def test_committed_spec_matches_the_app() -> None:
    assert main(["openapi", "--check"]) == 0


def test_a_stale_spec_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    stale = tmp_path / "openapi.json"
    spec = json.loads(openapi.spec_text())
    spec["paths"].pop("/healthz")
    stale.write_text(json.dumps(spec))
    monkeypatch.setattr(openapi, "SPEC_FILE", stale)
    assert main(["openapi", "--check"]) == 1
    assert "pnpm --filter @zm/api-client gen" in capsys.readouterr().err


def test_export_writes_the_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(openapi, "SPEC_FILE", target)
    assert main(["openapi"]) == 0
    spec = json.loads(target.read_text())
    assert spec["info"]["title"] == "Z-Matrix Platform API"
    assert "/healthz" in spec["paths"]
