"""The API's OpenAPI document, committed as packages/api-client/openapi.json (#21).

`uv run zm-api openapi` writes it, then `pnpm --filter @zm/api-client gen` regenerates the TS
client from it. CI runs `zm-api openapi --check` and diffs the generated client.
"""

import json
import sys
from pathlib import Path

from zm_api.main import create_app

PLATFORM = Path(__file__).resolve().parents[4]
SPEC_FILE = PLATFORM / "packages" / "api-client" / "openapi.json"


def spec_text() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def export(check: bool) -> int:
    text = spec_text()
    if check:
        current = SPEC_FILE.read_text() if SPEC_FILE.exists() else ""
        if current != text:
            print(
                f"{SPEC_FILE} is out of date: run `uv run zm-api openapi` "
                "and `pnpm --filter @zm/api-client gen`",
                file=sys.stderr,
            )
            return 1
        print(f"{SPEC_FILE} is up to date")
        return 0
    SPEC_FILE.write_text(text)
    print(f"wrote {SPEC_FILE}")
    return 0
