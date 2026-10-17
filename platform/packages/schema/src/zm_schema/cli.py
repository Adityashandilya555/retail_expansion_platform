"""`zm-schema validate <file>` and `zm-schema export [--check]`."""

import argparse
import json
import sys
from pathlib import Path

from pydantic.json_schema import GenerateJsonSchema

from zm_schema.api import validate
from zm_schema.models import WorkflowV1

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "workflow.schema.json"


class _NoFieldTitles(GenerateJsonSchema):
    """Only models get titles, so json-schema-to-typescript names types after the models."""

    def field_title_should_be_set(self, schema: object) -> bool:  # noqa: ARG002
        return False


def schema_text() -> str:
    schema = WorkflowV1.model_json_schema(by_alias=True, schema_generator=_NoFieldTitles)
    return json.dumps(schema, indent=2) + "\n"


def _inside_cwd(name: str) -> Path | None:
    """Resolve a user-supplied path; only files under the current directory are read."""
    base = Path.cwd().resolve()
    path = (base / name).resolve()
    return path if path.is_relative_to(base) and path.is_file() else None


def _validate_file(name: str) -> int:
    path = _inside_cwd(name)
    if path is None:
        print(f"{name}: not a file inside {Path.cwd()}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"{name}: {e}", file=sys.stderr)
        return 2
    errors = validate(doc)
    for e in errors:
        print(e)
    if not errors:
        print(f"{name}: valid")
    return 1 if errors else 0


def _export(check: bool) -> int:
    """Write (or with `check`, compare) the package's own `workflow.schema.json`."""
    text = schema_text()
    if check:
        current = SCHEMA_FILE.read_text() if SCHEMA_FILE.exists() else ""
        if current != text:
            print(
                f"{SCHEMA_FILE} is out of date: run `uv run zm-schema export` "
                "and `pnpm --filter @zm/schema gen`",
                file=sys.stderr,
            )
            return 1
        print(f"{SCHEMA_FILE} is up to date")
        return 0
    SCHEMA_FILE.write_text(text)
    print(f"wrote {SCHEMA_FILE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zm-schema", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate a workflow.json file (under the current dir)")
    v.add_argument("file")
    x = sub.add_parser("export", help="write workflow.schema.json from the models")
    x.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        return _validate_file(args.file)
    return _export(args.check)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
