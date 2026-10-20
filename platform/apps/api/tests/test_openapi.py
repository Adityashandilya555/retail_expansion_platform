"""packages/api-client/openapi.json is generated from the app; CI fails when it is stale (#21)."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from zm_api import openapi
from zm_api.cli import main
from zm_api.main import create_app


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


def test_errors_are_documented_as_problem_json_not_fastapis_422() -> None:
    app = create_app()

    class Body(BaseModel):
        name: str

    @app.post("/app/probe/{code}")
    async def probe(code: str, body: Body) -> Body:
        return body

    spec = app.openapi()
    responses = spec["paths"]["/app/probe/{code}"]["post"]["responses"]
    assert set(responses) == {"200", "default"}
    assert responses["default"]["content"] == {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
    }
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and "ValidationError" not in schemas
    assert schemas["Problem"]["required"] == [
        "type",
        "title",
        "status",
        "detail",
        "request_id",
        "errors",
    ]


def test_export_writes_the_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(openapi, "SPEC_FILE", target)
    assert main(["openapi"]) == 0
    spec = json.loads(target.read_text())
    assert spec["info"]["title"] == "Z-Matrix Platform API"
    assert "/healthz" in spec["paths"]
