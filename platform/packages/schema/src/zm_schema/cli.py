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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zm-schema", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate a workflow.json file")
    v.add_argument("file", type=Path)
    x = sub.add_parser("export", help="write workflow.schema.json from the models")
    x.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    x.add_argument("--out", type=Path, default=SCHEMA_FILE)
    args = parser.parse_args(argv)

    if args.cmd == "validate":
        try:
            doc = json.loads(args.file.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"{args.file}: {e}", file=sys.stderr)
            return 2
        errors = validate(doc)
        for e in errors:
            print(e)
        if not errors:
            print(f"{args.file}: valid")
        return 1 if errors else 0

    text = schema_text()
    if args.check:
        current = args.out.read_text() if args.out.exists() else ""
        if current != text:
            print(
                f"{args.out} is out of date: run `uv run zm-schema export` "
                "and `pnpm --filter @zm/schema gen`",
                file=sys.stderr,
            )
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.write_text(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
