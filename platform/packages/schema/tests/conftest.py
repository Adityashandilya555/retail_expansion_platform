import json
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).parent


def load_valid(name: str) -> dict[str, Any]:
    return json.loads((HERE / "valid" / f"{name}.json").read_text())


@pytest.fixture
def two_flows() -> dict[str, Any]:
    return load_valid("two_flows")


@pytest.fixture
def minimal() -> dict[str, Any]:
    return load_valid("minimal")
