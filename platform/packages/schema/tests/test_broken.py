import json
from pathlib import Path

import pytest

from zm_schema import validate

BROKEN = sorted((Path(__file__).parent / "broken").glob("*.json"))


def test_twenty_broken_samples() -> None:
    assert len(BROKEN) == 20


@pytest.mark.parametrize("path", BROKEN, ids=lambda p: p.stem)
def test_broken_sample_fails_at_expected_path(path: Path) -> None:
    sample = json.loads(path.read_text())
    errors = validate(sample["document"])
    assert [e.path for e in errors] == [sample["expected_path"]]
    assert sample["expected_message"] in errors[0].message
