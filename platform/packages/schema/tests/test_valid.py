import json
from pathlib import Path

import pytest

from zm_schema import WorkflowV1, migrate, validate

VALID = sorted((Path(__file__).parent / "valid").glob("*.json"))


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_documents(path: Path) -> None:
    doc = json.loads(path.read_text())
    assert validate(doc) == []
    model = migrate(doc)
    assert isinstance(model, WorkflowV1)
    assert model.model_dump(mode="json", by_alias=True, exclude_unset=True) == doc


def test_valid_fixtures_present() -> None:
    assert {p.stem for p in VALID} >= {"minimal", "two_flows", "matrix_retail_excerpt"}
