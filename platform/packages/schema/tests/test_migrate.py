from typing import Any

import pytest

from zm_schema import CURRENT_VERSION, SchemaVersionError, WorkflowV1, migrate, validate
from zm_schema import versioning as migrate_module


@pytest.mark.parametrize(
    ("version", "message"),
    [
        (2, "unknown schema_version 2 (supported: 1..1)"),
        (0, "unknown schema_version 0"),
        ("1", "must be an integer"),
        (None, "is required"),
    ],
)
def test_unknown_or_missing_version(minimal: dict[str, Any], version: Any, message: str) -> None:
    if version is None:
        minimal.pop("schema_version")
    else:
        minimal["schema_version"] = version
    errors = validate(minimal)
    assert [e.path for e in errors] == ["schema_version"]
    assert message in errors[0].message
    with pytest.raises(SchemaVersionError, match="schema_version"):
        migrate(minimal)


def test_current_version_passes(minimal: dict[str, Any]) -> None:
    assert CURRENT_VERSION == 1
    assert isinstance(migrate(minimal), WorkflowV1)


def test_registered_steps_run(monkeypatch: pytest.MonkeyPatch, minimal: dict[str, Any]) -> None:
    # Pretend v2 is current and v1 documents need their flow names upper-cased.
    def v1_to_v2(doc: dict[str, Any]) -> dict[str, Any]:
        for f in doc["flows"]:
            f["name"] = f["name"].upper()
        return doc

    monkeypatch.setattr(migrate_module, "CURRENT_VERSION", 2)
    monkeypatch.setitem(migrate_module.MIGRATIONS, 1, v1_to_v2)
    upgraded = migrate_module.upgrade(minimal)
    assert upgraded["schema_version"] == 2
    assert upgraded["flows"][0]["name"] == "SITE LAUNCH"


def test_missing_step_is_an_error(monkeypatch: pytest.MonkeyPatch, minimal: dict[str, Any]) -> None:
    monkeypatch.setattr(migrate_module, "CURRENT_VERSION", 2)
    with pytest.raises(SchemaVersionError, match="no migration registered"):
        migrate_module.upgrade(minimal)


def test_non_object_document() -> None:
    assert [e.path for e in validate([1, 2])] == ["$"]
