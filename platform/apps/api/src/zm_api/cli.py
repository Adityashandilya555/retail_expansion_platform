"""`zm-api templates sync`: upsert the starter templates into platform.templates (#18)."""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import templates


async def _sync() -> list[templates.Template]:
    found = templates.load_templates()
    engine = create_async_engine(templates.platform_database_url())
    try:
        async with engine.begin() as conn:
            await templates.sync(conn, found)
    finally:
        await engine.dispose()
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zm-api", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    tpl = sub.add_parser("templates", help="starter templates").add_subparsers(
        dest="action", required=True
    )
    tpl.add_parser("sync", help="validate and upsert packages/templates into platform.templates")
    parser.parse_args(argv)
    try:
        synced = asyncio.run(_sync())
    except templates.TemplateError as e:
        print(f"templates sync failed: {e}", file=sys.stderr)
        return 1
    for t in synced:
        print(f"synced {t.key} v{t.version}: {t.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
