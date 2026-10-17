import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Group:
    """An Operaton candidate group derived from a department level, e.g. `legal__L2`."""

    id: str
    name: str


@dataclass(frozen=True)
class FormSpec:
    """A JSON Schema 2020-12 for one form key plus its grid layout (also under `x-zm-layout`)."""

    schema: dict[str, Any]
    layout: list[dict[str, Any]]


@dataclass(frozen=True)
class Release:
    """Everything one publish deploys or stores (#19). Pure data; deterministic for one input."""

    bpmn: dict[str, bytes]
    """Resource name (`<process key>.bpmn`) → XML; all flows go into one Operaton deployment."""
    forms: dict[str, FormSpec]
    """Task id (or approval task id, or `start:<flow key>`) → form."""
    ui: dict[str, Any]
    """The document's `apps` section, unchanged; stored in the client's database, never in BPMN."""
    groups: list[Group]
    routing_vars: dict[str, list[str]]
    """Per task (and `start:<flow key>`): the variables its submission passes to Operaton (D5)."""
    process_keys: dict[str, str]
    """Flow key → main process key."""

    def canonical(self) -> dict[str, Any]:
        return {
            "bpmn": {k: v.decode() for k, v in self.bpmn.items()},
            "forms": {k: {"schema": f.schema, "layout": f.layout} for k, f in self.forms.items()},
            "ui": self.ui,
            "groups": [{"id": g.id, "name": g.name} for g in self.groups],
            "routing_vars": self.routing_vars,
            "process_keys": self.process_keys,
        }

    def digest(self) -> str:
        """sha256 over a canonical JSON form of the whole release."""
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()
