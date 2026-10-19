from typing import Any

from pydantic import ValidationError

from zm_schema.errors import Error, loc_to_path
from zm_schema.models import WorkflowV1
from zm_schema.semantics import semantic_errors
from zm_schema.versioning import SchemaVersionError, upgrade


def validate(doc: Any) -> list[Error]:
    """Every problem in a `workflow.json` document; `[]` means valid.

    Order: schema version, then structure (types, required keys), then cross-references.
    Semantic rules only run on a structurally valid document.
    """
    if not isinstance(doc, dict):
        return [Error("$", "a workflow document must be a JSON object")]
    try:
        doc = upgrade(dict(doc))
    except SchemaVersionError as e:
        return [Error("schema_version", str(e))]
    try:
        model = WorkflowV1.model_validate(doc, context={"structural_only": True})
    except ValidationError as e:
        return [Error(loc_to_path(x["loc"]), x["msg"]) for x in e.errors()]
    return semantic_errors(model)
