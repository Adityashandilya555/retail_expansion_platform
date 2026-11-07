import json
from pathlib import Path
from typing import Any

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


def test_a_stage_may_set_sla_days_a_positive_whole_number(minimal: dict[str, Any]) -> None:
    """S2-29: optional and additive (D14), so a document without it stays valid."""
    stage = minimal["flows"][0]["stages"][0]
    stage["sla_days"] = 5
    assert validate(minimal) == []
    assert migrate(minimal).flows[0].stages[0].sla_days == 5
    for bad in (0, -1, 1.5, "five"):
        stage["sla_days"] = bad
        assert [e.path for e in validate(minimal)] == ["flows[0].stages[0].sla_days"], bad
