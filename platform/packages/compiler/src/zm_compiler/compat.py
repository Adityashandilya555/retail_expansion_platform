"""`zm-compat --fixtures` (#31, D14): would every config we ship still publish and draw?

For every starter template (`packages/templates/*/workflow.json`) and every synthetic release
(`platform/tests/compat/*.json`): migrate, validate and compile it, then check each page against
the UI packages as they are now:
- its layout has a manifest (`packages/zm-blocks/layouts/<name>/manifest.json`) that the shell's
  registry (`packages/zm-blocks/src/registry.ts`) loads, and the page sets what it `requires`;
- each region is one of the layout's, holds no more than its `max`, and only `allowed_blocks`;
- each block has a manifest (`packages/zm-blocks/blocks/<name>/manifest.json`) that allows the
  region and whose `props` schema the page's props pass, or is announced in
  `packages/zm-blocks/planned.json` with the issue that builds it.

A planned block that already ships a manifest is a problem too, so the list can't go stale.
Real client configs never enter this public repo; live releases are checked at deploy (S2-09).
"""

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from zm_compiler.compile import CompileError, compile
from zm_schema import SchemaVersionError, migrate

PLATFORM = Path(__file__).resolve().parents[4]
BLOCKS_DIR = PLATFORM / "packages/zm-blocks"
TEMPLATES_DIR = PLATFORM / "packages/templates"
FIXTURES_DIR = PLATFORM / "tests/compat"
# the names the shell can draw: `layouts = { name: { manifest: …` and, by version (#27),
# `blocks = { name: { 1: { manifest: …`
_REGISTERED = re.compile(r"^\s*(\w+):\s*\{\s*(?:\d+:\s*\{\s*)?manifest:", re.M)


def registered(source: str, export: str) -> set[str]:
    """Names in `export const <export> … = { … }` of packages/zm-blocks/src/registry.ts."""
    found = re.search(
        rf"export const {export}\b[^=]*=\s*(?:\{{\}};|\{{(.*?)^\}};)", source, re.S | re.M
    )
    return set(_REGISTERED.findall(found.group(1) or "")) if found else set()


@dataclass
class Packages:
    """What the shell can draw now: manifests by name, and blocks announced but not built."""

    layouts: dict[str, dict[str, Any]]
    blocks: dict[str, dict[str, Any]]
    planned: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path | None = None) -> "Packages":
        root = root or BLOCKS_DIR

        def manifests(kind: str) -> dict[str, dict[str, Any]]:
            found = {}
            for path in sorted((root / kind).glob("*/manifest.json")):
                manifest = json.loads(path.read_text())
                found[manifest["name"]] = manifest
            return found

        planned_file = root / "planned.json"
        planned = json.loads(planned_file.read_text()) if planned_file.exists() else {}
        layouts, blocks = manifests("layouts"), manifests("blocks")
        # a manifest the shell's registry doesn't load can't be drawn: it doesn't count
        registry = root / "src/registry.ts"
        if registry.exists():
            source = registry.read_text()
            layouts = {k: v for k, v in layouts.items() if k in registered(source, "layouts")}
            blocks = {k: v for k, v in blocks.items() if k in registered(source, "blocks")}
        return cls(layouts, blocks, planned)


@dataclass(frozen=True)
class Problem:
    source: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.source} · {self.where}: {self.message}"


def _block_problems(packages: Packages, region: str, block: dict[str, Any]) -> list[str]:
    """Problems with one placed block; a planned block has none (it is counted instead)."""
    name = block.get("block", "")
    manifest = packages.blocks.get(name)
    if manifest is None:
        if name in packages.planned:
            return []
        return [f"block {name!r} (region {region}) has no manifest and isn't planned"]
    problems = []
    if region not in manifest.get("regions", []):
        problems.append(f"block {name!r} can't go in region {region!r}")
    schema = manifest.get("props") or {"type": "object"}
    for error in Draft202012Validator(schema).iter_errors(block.get("props", {})):
        at = "/".join(str(p) for p in error.absolute_path) or "props"
        problems.append(f"block {name!r} prop {at}: {error.message}")
    return problems


def check_pages(
    source: str,
    ui: dict[str, Any],
    packages: Packages,
    planned_uses: dict[str, int] | None = None,
) -> list[Problem]:
    """Every page of a release's `ui` (the compiled `apps` section) against the packages.
    Planned blocks found are counted into `planned_uses`."""
    out: list[Problem] = []
    for page in ui.get("pages", []):
        where = f"page {page.get('key')}"
        layout = packages.layouts.get(page.get("layout", ""))
        if layout is None:
            out.append(Problem(source, where, f"layout {page.get('layout')!r} has no manifest"))
            continue
        for required in layout.get("requires", []):
            if not page.get(required):
                out.append(Problem(source, where, f"layout {layout['name']!r} needs {required}"))
        regions = {r["key"]: r for r in layout.get("regions", [])}
        for key, placed in page.get("regions", {}).items():
            region = regions.get(key)
            if region is None:
                msg = f"layout {layout['name']!r} has no region {key!r}"
                out.append(Problem(source, where, msg))
                continue
            if "max" in region and len(placed) > region["max"]:
                out.append(Problem(source, where, f"region {key!r} holds at most {region['max']}"))
            for block in placed:
                name = block.get("block")
                allowed = region.get("allowed_blocks")
                if allowed is not None and name not in allowed:
                    out.append(
                        Problem(source, where, f"region {key!r} doesn't take block {name!r}")
                    )
                if planned_uses is not None and name in packages.planned:
                    planned_uses[name] = planned_uses.get(name, 0) + 1
                out += [Problem(source, where, m) for m in _block_problems(packages, key, block)]
    return out


def check_document(
    source: str, doc: Any, packages: Packages, planned_uses: dict[str, int] | None = None
) -> list[Problem]:
    """Migrate, validate and compile one workflow.json, then check its pages."""
    try:
        release = compile(migrate(doc), "compat-check")
    except (SchemaVersionError, CompileError, ValueError) as e:  # ValidationError is a ValueError
        return [Problem(source, "document", f"doesn't compile: {e}")]
    return check_pages(source, release.ui, packages, planned_uses)


def check_packages(packages: Packages) -> list[Problem]:
    stale = sorted(set(packages.planned) & set(packages.blocks))
    return [
        Problem("planned.json", name, "has a manifest now: remove it from planned.json")
        for name in stale
    ]


def sources(fixtures: bool) -> Iterable[tuple[str, Path]]:
    for path in sorted(TEMPLATES_DIR.glob("*/workflow.json")):
        yield f"template {path.parent.name}", path
    if fixtures:
        for path in sorted(FIXTURES_DIR.glob("*.json")):
            yield f"fixture {path.stem}", path


def run(fixtures: bool, packages: Packages | None = None) -> tuple[list[Problem], list[str]]:
    """(problems, notes); the notes say which planned blocks the configs use, and where from."""
    packages = packages or Packages.load()
    problems, uses = check_packages(packages), {}
    for source, path in sources(fixtures):
        problems += check_document(source, json.loads(path.read_text()), packages, uses)
    notes = [
        f"block {name!r} is placed {n}× and planned in {packages.planned[name]}"
        for name, n in sorted(uses.items())
    ]
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zm-compat",
        description="Check every template (and fixture) still publishes and draws.",
    )
    parser.add_argument(
        "--fixtures", action="store_true", help="also the synthetic releases in tests/compat"
    )
    args = parser.parse_args(argv)
    problems, notes = run(args.fixtures)
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    checked = len(list(sources(args.fixtures)))
    if problems:
        print(f"zm-compat: {len(problems)} problem(s) in {checked} configs", file=sys.stderr)
        return 1
    print(f"zm-compat: {checked} configs compile and draw with the current packages")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
