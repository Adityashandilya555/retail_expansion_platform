"""Field versioning (#35, S2-25): fields and document types change additively, by key.

Compared with every published release's fields (`releases.published_fields`), each field as the
newest release that has it defines it, so reactivating an older release doesn't lift the rules
for the fields a newer one added:
- adding a field, a document type (a `document` field) or a select option is allowed;
- a label may change, the key never (a field's key is its name in `fields`);
- a published field is removed by hiding it (`hidden: true`), never deleted, so the values stored
  under its key keep their meaning;
- a type change needs a migration decision (its UI is Backlog), so it is refused;
- making a field required binds sites started from then on: a site keeps its own release's forms;
- removing a select option and changing a document field's rules are allowed: a stored value keeps
  its raw option id, a site keeps its own release's forms, and document rules apply to new uploads.

`errors` runs on every draft change (`drafts.patched`), on undo and at publish; `stamp` is
publish's. (`zm_schema.versioning` is something else: schema_version upgrades, D14.)
"""

from collections.abc import Mapping, Sequence
from typing import Any

from zm_schema import Error


def _newest(published: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Each published key's field as the newest release that has it defines it."""
    return {key: field for released in published for key, field in released.items()}


def errors(published: Sequence[Mapping[str, Any]], draft: Mapping[str, Any]) -> list[Error]:
    """What `draft` does to the published fields (each release's, oldest first) that the rules
    refuse; none before the first publish. A type is checked against the newest release that has
    the key, so a type changed before these rules existed doesn't block every publish."""
    found = draft.get("fields")
    fields = found if isinstance(found, dict) else {}  # a broken draft: validate says why
    out = []
    for key, was in _newest(published).items():
        now = fields.get(key)
        if not isinstance(now, dict):
            out.append(
                Error(
                    f"fields.{key}",
                    f"{key} is in a published release: hide it instead of deleting it, so its"
                    " stored values stay readable",
                )
            )
        elif now.get("type") != was["type"]:
            out.append(
                Error(
                    f"fields.{key}.type",
                    f"{key} is a {was['type']} in a published release: changing its type needs"
                    " a migration decision; add a new field instead",
                )
            )
    return out


def stamp(published: Sequence[Mapping[str, Any]], workflow: dict[str, Any], version: int) -> None:
    """Stamp `workflow`, about to be recorded as release `version`: a field no release had gets
    `since`, a field required from this release on `required_from_version`; the others keep the
    stamps of the newest release that has them. Whatever the draft said is replaced. The first
    release is the baseline: nothing is stamped."""
    old = _newest(published)
    for key, field in workflow.get("fields", {}).items():
        was = old.get(key)
        stamps = {"since": None, "required_from_version": None}
        if published:
            stamps["since"] = was.get("since") if was else version
            if field.get("required"):
                stamps["required_from_version"] = (
                    was.get("required_from_version") if was and was.get("required") else version
                )
        for name, value in stamps.items():
            if value is None:
                field.pop(name, None)
            else:
                field[name] = value
