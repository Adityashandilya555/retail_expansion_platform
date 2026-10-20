"""`zm-api templates sync`: upsert the starter templates into platform.templates (#18).
`zm-api openapi [--check]`: write (or compare) packages/api-client/openapi.json (#21).
`zm-api owners create --email … --name …`: the first platform owner; asks for the password (#22).
`zm-api users seed --client <slug> <file>`: demo users, mirrored to the client's Operaton (#22).
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from zm_api import openapi, templates, users
from zm_api.context import PlatformRegistry, Resolver


async def _sync() -> list[templates.Template]:
    found = templates.load_templates()
    engine = create_async_engine(templates.platform_database_url())
    try:
        async with engine.begin() as conn:
            await templates.sync(conn, found)
    finally:
        await engine.dispose()
    return found


def _templates() -> int:
    try:
        synced = asyncio.run(_sync())
    except templates.TemplateError as e:
        print(f"templates sync failed: {e}", file=sys.stderr)
        return 1
    for t in synced:
        print(f"synced {t.key} v{t.version}: {t.name}")
    return 0


def _create_owner(email: str, name: str) -> int:
    password = getpass.getpass("Password: ")
    if len(password) < 12 or password != getpass.getpass("Again: "):
        print("passwords must match and be at least 12 characters", file=sys.stderr)
        return 1

    async def go():
        platform = PlatformRegistry()
        try:
            return await users.create_owner(platform, email, name, password)
        finally:
            await platform.aclose()

    try:
        owner_id = asyncio.run(go())
    except IntegrityError:
        print(f"an owner with email {email} already exists", file=sys.stderr)
        return 1
    print(f"created owner {email} ({owner_id})")
    return 0


class _SeedUser(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1)
    department: str = Field(min_length=1)
    level: int = Field(ge=1)


class _SeedFile(BaseModel):
    users: list[_SeedUser]


def _inside_cwd(name: str) -> Path | None:
    """Resolve a user-supplied path; only files under the current directory are read
    (as `zm-schema validate` does)."""
    base = Path.cwd().resolve()
    path = (base / name).resolve()
    return path if path.is_relative_to(base) and path.is_file() else None


def _seed(slug: str, name: str) -> int:
    path = _inside_cwd(name)
    if path is None:
        print(f"{name}: not a file inside {Path.cwd()}", file=sys.stderr)
        return 2
    try:
        people = [u.model_dump() for u in _SeedFile.model_validate_json(path.read_text()).users]
    except (OSError, ValidationError) as e:
        print(f"{name}: {e}", file=sys.stderr)
        return 2

    async def go() -> list[users.Seeded] | None:
        resolver = Resolver(PlatformRegistry())
        try:
            ctx = await resolver.resolve(slug)
            return None if ctx is None else await users.seed_users(ctx, people)
        finally:
            await resolver.aclose()

    seeded = asyncio.run(go())
    if seeded is None:
        print(f"no client {slug}", file=sys.stderr)
        return 1
    # passwords of new users are shown once, here; existing users keep theirs
    for s in seeded:
        print(f"{s.email}\t{s.password or '(unchanged)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zm-api", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    tpl = sub.add_parser("templates", help="starter templates").add_subparsers(
        dest="action", required=True
    )
    tpl.add_parser("sync", help="validate and upsert packages/templates into platform.templates")
    spec = sub.add_parser("openapi", help="write packages/api-client/openapi.json from the app")
    spec.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    owners = sub.add_parser("owners", help="platform owners").add_subparsers(
        dest="action", required=True
    )
    create = owners.add_parser("create", help="create an owner (asks for the password)")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Owner")
    people = sub.add_parser("users", help="client users").add_subparsers(
        dest="action", required=True
    )
    seed = people.add_parser("seed", help="load demo users (until invites exist, S2-23)")
    seed.add_argument("--client", required=True, help="client slug")
    seed.add_argument("file", help='JSON {"users": [{email, name, department, level}]}')
    args = parser.parse_args(argv)
    if args.cmd == "openapi":
        return openapi.export(args.check)
    if args.cmd == "owners":
        return _create_owner(args.email, args.name)
    if args.cmd == "users":
        return _seed(args.client, args.file)
    return _templates()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
