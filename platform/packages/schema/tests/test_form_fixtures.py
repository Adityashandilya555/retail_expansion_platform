"""Shared form fixtures (#28): the API reports the same JSON Pointer paths as @zm/forms.

The same `*.cases.json` run in vitest (packages/zm-forms/src/validate.test.ts). The schemas are
compiler output, kept fresh by packages/compiler/tests/test_golden.py.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from zm_schema import submission_errors

FIXTURES = Path(__file__).parent / "fixtures"


def cases() -> list[Any]:
    out = []
    for path in sorted(FIXTURES.glob("*.cases.json")):
        form = path.name.removesuffix(".cases.json")
        schema = json.loads((FIXTURES / f"{form}.schema.json").read_text())
        for case in json.loads(path.read_text()):
            out.append(pytest.param(schema, case, id=f"{form}: {case['name']}"))
    return out


@pytest.mark.parametrize(("schema", "case"), cases())
def test_same_paths_as_the_browser(schema: dict[str, Any], case: dict[str, Any]) -> None:
    errors = submission_errors(schema, case["values"])
    assert [e["path"] for e in errors] == case["paths"]
    assert all(e["message"] for e in errors)


def test_every_fixture_has_cases() -> None:
    assert len(cases()) >= 15


def test_pointers_escape_tilde_and_slash() -> None:
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    assert [e["path"] for e in submission_errors(schema, {"a/b~c": 1})] == ["/a~1b~0c"]
