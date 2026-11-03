"""`zm-compat --fixtures` (#31, D14): would every config we ship still publish and draw?

For every starter template (`packages/templates/*/workflow.json`) and every synthetic release
(`platform/tests/compat/*.json`): validate and compile it. Validation checks each page against
the UI packages as they are now (`packages/zm-blocks/catalogue.json`, #37): its layout exists and
the page sets what it `requires`; each region is one of the layout's, holds no more than its
`max` and only blocks it takes; each block's props pass its manifest's schema.

Real client configs never enter this public repo; live releases are checked at deploy (S2-09).
"""

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zm_compiler.compile import CompileError, compile
from zm_schema import migrate, validate

PLATFORM = Path(__file__).resolve().parents[4]
TEMPLATES_DIR = PLATFORM / "packages/templates"
FIXTURES_DIR = PLATFORM / "tests/compat"


@dataclass(frozen=True)
class Problem:
    source: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.source} · {self.where}: {self.message}"


def check_document(source: str, doc: Any) -> list[Problem]:
    """Validate and compile one workflow.json."""
    errors = validate(doc)
    if errors:
        return [Problem(source, e.path, e.message) for e in errors]
    try:
        compile(migrate(doc), "compat-check")
    except CompileError as e:
        return [Problem(source, "document", f"doesn't compile: {e}")]
    return []


def sources(fixtures: bool) -> Iterable[tuple[str, Path]]:
    for path in sorted(TEMPLATES_DIR.glob("*/workflow.json")):
        yield f"template {path.parent.name}", path
    if fixtures:
        for path in sorted(FIXTURES_DIR.glob("*.json")):
            yield f"fixture {path.stem}", path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zm-compat",
        description="Check every template (and fixture) still publishes and draws.",
    )
    parser.add_argument(
        "--fixtures", action="store_true", help="also the synthetic releases in tests/compat"
    )
    args = parser.parse_args(argv)
    found = list(sources(args.fixtures))
    problems = [
        p for source, path in found for p in check_document(source, json.loads(path.read_text()))
    ]
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(f"zm-compat: {len(problems)} problem(s) in {len(found)} configs", file=sys.stderr)
        return 1
    print(f"zm-compat: {len(found)} configs compile and draw with the current packages")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
