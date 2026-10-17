"""`workflow.json` schema v1 (#16, S1-07): models, validation and the versioning hook."""

from zm_schema.api import validate
from zm_schema.errors import Error
from zm_schema.models import WorkflowV1
from zm_schema.semantics import group_id
from zm_schema.versioning import CURRENT_VERSION, SchemaVersionError, migrate

__version__ = "0.1.0"

__all__ = [
    "CURRENT_VERSION",
    "Error",
    "SchemaVersionError",
    "WorkflowV1",
    "group_id",
    "migrate",
    "validate",
]
