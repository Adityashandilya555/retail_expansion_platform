import copy
import json
from pathlib import Path
from typing import Any

import pytest

from zm_schema import WorkflowV1, migrate

PLATFORM = Path(__file__).resolve().parents[3]
TEMPLATES = PLATFORM / "packages" / "templates"
GOLDEN = Path(__file__).parent / "golden"

TWO_STAGE: dict[str, Any] = {
    "schema_version": 1,
    "org": {
        "departments": [
            {
                "key": "bd",
                "name": "BD",
                "levels": [{"n": 1, "name": "Exec"}, {"n": 2, "name": "Sup"}],
            },
            {
                "key": "legal",
                "name": "Legal",
                "levels": [{"n": 1, "name": "Exec"}, {"n": 2, "name": "Sup"}],
            },
        ]
    },
    "flows": [
        {
            "key": "store_launch",
            "name": "Store launch",
            "case_label": "Site",
            "case_code": {"pattern": "T-{seq:3}"},
            "start": {
                "performers": [{"department": "bd", "level": 1, "or_above": True}],
                "form": "start",
            },
            "stages": [
                {
                    "key": "bd",
                    "name": "BD",
                    "department": "bd",
                    "tasks": [
                        {"id": "bd_task", "name": "BD task", "performer": {"initiator": True}}
                    ],
                },
                {
                    "key": "legal",
                    "name": "Legal",
                    "department": "legal",
                    "after": ["bd"],
                    "tasks": [
                        {
                            "id": "legal_review",
                            "name": "Legal review",
                            "performer": {"department": "legal", "level": 1, "or_above": True},
                            "outcomes": [
                                {"id": "approve", "label": "Approve"},
                                {
                                    "id": "reject",
                                    "label": "Reject",
                                    "to": "@close",
                                    "comment_required": True,
                                },
                            ],
                        }
                    ],
                },
            ],
        }
    ],
    "fields": {"site_name": {"type": "text", "label": "Site name", "required": True}},
    "forms": {
        "start": {"sections": [{"key": "s", "title": "Site", "rows": [[{"field": "site_name"}]]}]}
    },
}


def doc(data: dict[str, Any]) -> WorkflowV1:
    return migrate(copy.deepcopy(data))


def template(key: str) -> WorkflowV1:
    return migrate(json.loads((TEMPLATES / key / "workflow.json").read_text()))


@pytest.fixture
def two_stage() -> dict[str, Any]:
    return copy.deepcopy(TWO_STAGE)
