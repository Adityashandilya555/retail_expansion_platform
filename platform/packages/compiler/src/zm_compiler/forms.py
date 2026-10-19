"""Forms → JSON Schema 2020-12 (#19). The shell (#28) renders from `title`, `x-zm-type`,
`x-zm-options` and `x-zm-layout`; the API validates submissions against the same schema.
"""

from typing import Any

from zm_compiler.release import FormSpec
from zm_schema.models import FieldDef, Form, Outcome, WorkflowV1

DIALECT = "https://json-schema.org/draft/2020-12/schema"
_DATE = {"type": "string", "format": "date"}


def field_schema(key: str, f: FieldDef) -> dict[str, Any] | None:
    """JSON Schema for one field value; `None` for display-only fields (`info`)."""
    opts = [o.id for o in f.options or []]
    t = f.type
    if t == "info":
        return None
    if t in {"text", "textarea", "user"}:
        s: dict[str, Any] = {"type": "string"}
    elif t in {"number", "currency", "percent"}:
        s = {"type": "number"}
    elif t == "rating":
        s = {"type": "integer", "minimum": 1, "maximum": 5}
    elif t == "date":
        s = dict(_DATE)
    elif t == "date_range":
        s = {
            "type": "object",
            "properties": {"from": _DATE, "to": _DATE},
            "required": ["from", "to"],
            "additionalProperties": False,
        }
    elif t == "select":
        s = {"type": "string", "enum": opts}
    elif t == "multi_select":
        s = {"type": "array", "items": {"enum": opts}, "uniqueItems": True}
    elif t == "yes_no":
        s = {"type": "boolean"}
    elif t == "address":
        s = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "lat": {"type": "number"},
                "lng": {"type": "number"},
            },
            "required": ["text"],
            "additionalProperties": False,
        }
    elif t == "checklist":
        s = {
            "type": "object",
            "properties": {o: {"enum": ["yes", "no", "na"]} for o in opts},
            "additionalProperties": False,
        }
    elif t == "line_items":
        s = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["label", "amount"],
                "additionalProperties": False,
            },
        }
    elif t == "document":
        s = {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "name": {"type": "string"},
            },
            "required": ["key", "sha256", "name"],
            "additionalProperties": False,
        }
        if f.document:
            s["x-zm-document"] = f.document.model_dump(mode="json")
    else:  # computed
        s = {"type": "number", "readOnly": True}
    s["title"] = f.label
    s["x-zm-type"] = t
    if f.options:
        s["x-zm-options"] = [o.model_dump(mode="json") for o in f.options]
    return s


def _layout(form: Form | None) -> list[dict[str, Any]]:
    if form is None:
        return []
    return [s.model_dump(mode="json", exclude_none=True) for s in form.sections]


def build_form(
    wf: WorkflowV1,
    form: Form | None,
    *,
    outcome_var: str | None = None,
    outcomes: list[Outcome] | None = None,
    show: list[str] | None = None,
) -> FormSpec:
    props: dict[str, Any] = {}
    required: list[str] = []
    if form is not None:
        for section in form.sections:
            for row in section.rows:
                for cell in row:
                    f = wf.fields[cell.field]
                    if f.hidden or cell.field in props:
                        continue
                    s = field_schema(cell.field, f)
                    if s is None:
                        continue
                    props[cell.field] = s
                    if f.required and f.type != "computed":
                        required.append(cell.field)
    schema: dict[str, Any] = {"$schema": DIALECT, "type": "object", "properties": props}
    if outcome_var and outcomes:
        comment_var = outcome_var.removesuffix("_outcome") + "_comment"
        props[outcome_var] = {
            "type": "string",
            "enum": [o.id for o in outcomes],
            "title": "Decision",
            "x-zm-type": "outcome",
            "x-zm-options": [{"id": o.id, "label": o.label} for o in outcomes],
        }
        props[comment_var] = {"type": "string", "title": "Comment", "x-zm-type": "textarea"}
        required.append(outcome_var)
        needs_comment = [o.id for o in outcomes if o.comment_required]
        if needs_comment:
            schema["if"] = {
                "properties": {outcome_var: {"enum": needs_comment}},
                "required": [outcome_var],
            }
            schema["then"] = {
                "properties": {comment_var: {"minLength": 1}},
                "required": [comment_var],
            }
    schema["required"] = required
    schema["additionalProperties"] = False
    layout = _layout(form)
    schema["x-zm-layout"] = layout
    if show:
        schema["x-zm-show"] = list(show)
    return FormSpec(schema=schema, layout=layout)
