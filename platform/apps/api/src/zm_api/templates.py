"""Starter templates (#18): `packages/templates/<key>/workflow.json` → `platform.templates`.

A template is only a starting point (D14): provisioning copies it into a new client's draft once,
and later template changes never reach existing clients.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# Fixed location in the repo; never a user-supplied path.
TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "packages" / "templates"
TEMPLATE_VERSION = 1
TEMPLATE_NAMES = {
    "matrix-retail": "Matrix Retail: gated retail store launch",
    "qsr": "QSR store launch (local isolation)",
}

_UPSERT = text(
    "insert into platform.templates (key, version, name, workflow_json) "
    "values (:key, :version, :name, cast(:workflow_json as jsonb)) "
    "on conflict (key, version) do update "
    "set name = excluded.name, workflow_json = excluded.workflow_json"
)


@dataclass(frozen=True)
class Template:
    key: str
    version: int
    name: str
    workflow: dict[str, Any]


class TemplateError(ValueError):
    pass


def load_templates() -> list[Template]:
    """Every `<key>/workflow.json` under the templates folder, validated against `WorkflowV1`."""
    from zm_schema import validate  # CLI-only dependency: the API server never imports it

    templates = []
    for path in sorted(TEMPLATES_DIR.glob("*/workflow.json")):
        key = path.parent.name
        doc = json.loads(path.read_text())
        errors = validate(doc)
        if errors:
            raise TemplateError(f"{key}: " + "; ".join(str(e) for e in errors))
        templates.append(Template(key, TEMPLATE_VERSION, TEMPLATE_NAMES.get(key, key), doc))
    return templates


async def sync(conn: AsyncConnection, templates: list[Template]) -> None:
    """Upsert one row per (key, version); running it again changes nothing."""
    for t in templates:
        await conn.execute(
            _UPSERT,
            {
                "key": t.key,
                "version": t.version,
                "name": t.name,
                "workflow_json": json.dumps(t.workflow),
            },
        )


def platform_database_url() -> str:
    url = os.environ.get("PLATFORM_DATABASE_URL")
    if not url:
        raise TemplateError("PLATFORM_DATABASE_URL is not set")
    return url
