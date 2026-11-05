"""Field versioning (#35, S2-25): fields and document types change additively, by key.

Compared with the active release's workflow, field by field:
- adding a field, a document type (a `document` field) or a select option is allowed;
- a label may change, the key never (a field's key is its name in `fields`);
- a published field is removed by hiding it (`hidden: true`), never deleted, so the values stored
  under its key keep their meaning;
- a type change needs a migration decision (its UI is Backlog), so it is refused;
- making a field required binds sites started from then on: a site keeps its own release's forms.

`errors` runs on every draft change (`drafts.patched`) and again at publish; `stamp` is publish's.
(`zm_schema.versioning` is something else: schema_version upgrades, D14.)
"""

from collections.abc import Mapping
from typing import Any

from zm_schema import Error


def errors(live: Mapping[str, Any] | None, draft: Mapping[str, Any]) -> list[Error]:
    """What `draft` does to the live release's fields that the rules refuse; none before the
    first publish."""
    if live is None:
        return []
    found = draft.get("fields")
    fields = found if isinstance(found, dict) else {}  # a broken draft: validate says why
    out = []
    for key, was in live.get("fields", {}).items():
        now = fields.get(key)
        if not isinstance(now, dict):
            out.append(
                Error(
                    f"fields.{key}",
                    f"{key} is in the live release: hide it instead of deleting it, so its stored"
                    " values stay readable",
                )
            )
        elif now.get("type") != was["type"]:
            out.append(
                Error(
                    f"fields.{key}.type",
                    f"{key} is a {was['type']} in the live release: changing its type needs a"
                    " migration decision; add a new field instead",
                )
            )
    return out


def stamp(live: Mapping[str, Any] | None, workflow: dict[str, Any], version: int) -> None:
    """Stamp `workflow`, about to be recorded as release `version`: a field the live release
    didn't have gets `since`, a field required from this release on `required_from_version`;
    the others keep the live release's stamps. Whatever the draft said is replaced. The first
    release is the baseline: nothing is stamped."""
    if live is None:
        return
    old = live.get("fields", {})
    for key, field in workflow.get("fields", {}).items():
        was = old.get(key)
        required_from = None
        if field.get("required"):
            required_from = (
                was.get("required_from_version") if was and was.get("required") else version
            )
        stamps = {
            "since": was.get("since") if was else version,
            "required_from_version": required_from,
        }
        for name, value in stamps.items():
            if value is None:
                field.pop(name, None)
            else:
                field[name] = value
