"""The UI packages the shell can draw (#37): `packages/zm-blocks/catalogue.json`.

Generated from the shell's registry (`packages/zm-blocks/src/catalogue.test.ts`): every layout with
its region rules (`allowed_blocks`, `max`, `requires`) and every registered version of every block
with its props schema. `validate()` checks a document's pages against it; the agent's tools (#39)
list it. The API image copies it to the same place (`apps/api/Dockerfile`).
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

CATALOGUE_FILE = Path(__file__).resolve().parents[3] / "zm-blocks" / "catalogue.json"


@cache
def load() -> dict[str, Any]:
    """`{"layouts": {name: manifest}, "blocks": {name: {version: manifest}}}`; shared, don't
    change it."""
    return json.loads(CATALOGUE_FILE.read_text())
