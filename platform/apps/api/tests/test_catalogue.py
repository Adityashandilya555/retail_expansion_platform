"""catalogue.json (#37): what the agent's tools (#39) read has the names and versions of the layout
and block manifests, which the shell registers one to one (zm-blocks `src/manifests.test.ts`)."""

import json
from pathlib import Path

from zm_schema import catalogue

ZM_BLOCKS = catalogue.CATALOGUE_FILE.parent


def manifests(kind: str) -> dict[str, int]:
    found = (json.loads(p.read_text()) for p in ZM_BLOCKS.glob(f"{kind}/*/manifest.json"))
    return {m["name"]: m["version"] for m in found}


def test_the_catalogue_has_every_registered_layout_and_block() -> None:
    loaded = catalogue.load()
    assert {n: m["version"] for n, m in loaded["layouts"].items()} == manifests("layouts")
    assert {n: m["version"] for n, m in loaded["blocks"].items()} == manifests("blocks")
    assert {"standard", "case"} <= set(loaded["layouts"]) and "task_inbox" in loaded["blocks"]


def test_the_api_image_ships_it() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert "COPY packages/zm-blocks/catalogue.json packages/zm-blocks/catalogue.json" in dockerfile
