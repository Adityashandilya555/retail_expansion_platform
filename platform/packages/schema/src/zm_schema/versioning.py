"""Versioning hook (D14): upgrade older `workflow.json` documents to the current schema.

A breaking schema change bumps `CURRENT_VERSION` and registers a step `MIGRATIONS[n]` that turns
a version-n document into version n+1; clients are migrated one at a time. Empty at v1.
"""

from collections.abc import Callable
from typing import Any

from zm_schema.models import WorkflowV1

CURRENT_VERSION = 1
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class SchemaVersionError(ValueError):
    pass


def check_version(doc: dict[str, Any]) -> int:
    version = doc.get("schema_version")
    if type(version) is not int:
        raise SchemaVersionError(
            f"schema_version is required and must be an integer (supported: 1..{CURRENT_VERSION})"
        )
    if version > CURRENT_VERSION or version < 1:
        raise SchemaVersionError(
            f"unknown schema_version {version} (supported: 1..{CURRENT_VERSION})"
        )
    return version


def upgrade(doc: dict[str, Any]) -> dict[str, Any]:
    """Apply registered steps until the document is at `CURRENT_VERSION` (no validation)."""
    version = check_version(doc)
    while version < CURRENT_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise SchemaVersionError(f"no migration registered from schema_version {version}")
        doc = step(doc)
        version += 1
        doc["schema_version"] = version
    return doc


def migrate(doc: dict[str, Any]) -> WorkflowV1:
    """Upgrade and validate; raises `SchemaVersionError` or pydantic's `ValidationError`."""
    return WorkflowV1.model_validate(upgrade(doc))
