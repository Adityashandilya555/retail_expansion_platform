"""Submission validation against a compiled form (#19 `FormSpec.schema`), for the API's 422 (#26).

Paths and messages mirror @zm/forms (packages/zm-forms/src/validate.ts); the shared fixtures in
tests/fixtures/*.cases.json keep the two in step (#28).
"""

from typing import Any

from jsonschema import Draft202012Validator


def _pointer(parts: list[Any]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def submission_errors(schema: dict[str, Any], values: Any) -> list[dict[str, str]]:
    """`[{path, message}]` sorted by JSON Pointer, one per path; `[]` when valid.

    A missing or unknown property is reported at that property (as Ajv's `missingProperty` /
    `additionalProperty`), every other error where it occurs.
    """
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    out: dict[str, str] = {}
    for e in validator.iter_errors(values):
        at = list(e.absolute_path)
        if e.validator == "required":
            for p in e.validator_value:
                if p not in e.instance:
                    out.setdefault(_pointer([*at, p]), "Required")
        elif e.validator == "additionalProperties":
            for p in set(e.instance) - set(e.schema.get("properties", {})):
                out.setdefault(_pointer([*at, p]), "Not a field of this form")
        elif e.validator == "minLength" and e.validator_value == 1:
            out.setdefault(_pointer(at), "Required")
        elif e.validator == "format" and e.validator_value == "date":
            out.setdefault(_pointer(at), "Enter a valid date")
        elif e.validator == "enum":
            out.setdefault(_pointer(at), "Choose one of the options")
        else:
            out.setdefault(_pointer(at), e.message)
    return [{"path": p, "message": out[p]} for p in sorted(out)]
