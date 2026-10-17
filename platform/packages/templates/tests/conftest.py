import json
from pathlib import Path
from typing import Any

import pytest

TEMPLATES = Path(__file__).resolve().parents[1]
PROTOTYPE = TEMPLATES.parents[1] / "prototype" / "workspace.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def matrix_retail() -> dict[str, Any]:
    return load(TEMPLATES / "matrix-retail" / "workflow.json")


@pytest.fixture(scope="session")
def qsr() -> dict[str, Any]:
    return load(TEMPLATES / "qsr" / "workflow.json")


@pytest.fixture(scope="session")
def prototype() -> dict[str, Any]:
    """The prototype's workspace.json, read as data only (never imported or run; #8)."""
    return load(PROTOTYPE)
