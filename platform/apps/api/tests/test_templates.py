import json
from pathlib import Path

import pytest

from zm_api import cli, templates


def test_loads_both_templates() -> None:
    found = templates.load_templates()
    assert [t.key for t in found] == ["matrix-retail", "qsr"]
    assert all(t.version == 1 and t.workflow["schema_version"] == 1 for t in found)
    assert found[0].name == templates.TEMPLATE_NAMES["matrix-retail"]


def test_invalid_template_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "workflow.json").write_text(json.dumps({"schema_version": 1}))
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path)
    with pytest.raises(templates.TemplateError, match="broken: org"):
        templates.load_templates()


def test_sync_needs_a_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PLATFORM_DATABASE_URL", raising=False)
    assert cli.main(["templates", "sync"]) == 1
    assert "PLATFORM_DATABASE_URL is not set" in capsys.readouterr().err
