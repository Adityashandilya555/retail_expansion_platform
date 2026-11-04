import hashlib
import logging
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from zm_worker.dsn import libpq

log = logging.getLogger("zm_worker.vault_migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "infra/supabase/client/migrations"
ROLES = ("zm_engine", "zm_api")


class MigrationChangedError(RuntimeError):
    """An already-applied migration file was edited."""


def apply(dsn: str, secrets: dict[str, str], migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, set the role passwords, return the names applied this run."""
    missing = [role for role in ROLES if not secrets.get(role)]
    if missing:
        raise ValueError(f"no password given for {', '.join(missing)}")
    files = sorted(migrations_dir.glob("*.sql"))  # NNNN_ prefix sorts in order
    if not files:  # e.g. an image without infra/supabase/client: fail, don't say "up to date"
        raise FileNotFoundError(f"no migrations in {migrations_dir}")

    applied: list[str] = []
    with psycopg.connect(libpq(dsn), autocommit=True) as conn:
        conn.execute("create schema if not exists app")
        conn.execute(
            "create table if not exists app.schema_migrations ("
            " name text primary key,"
            " applied_at timestamptz not null default now(),"
            " sha256 text not null)"
        )
        done = dict(conn.execute("select name, sha256 from app.schema_migrations").fetchall())

        for path in files:
            body = path.read_text()
            sha = hashlib.sha256(body.encode()).hexdigest()
            if path.name in done:
                if done[path.name] != sha:
                    raise MigrationChangedError(
                        f"{path.name} was already applied (sha256 {done[path.name][:12]}) but the "
                        f"file now hashes to {sha[:12]}: add a new migration instead of editing it"
                    )
                continue
            with conn.transaction():
                conn.execute(body)  # no params → multiple statements allowed
                conn.execute(
                    "insert into app.schema_migrations (name, sha256) values (%s, %s)",
                    (path.name, sha),
                )
            log.info("applied %s", path.name)
            applied.append(path.name)

        for role in ROLES:
            hashed = conn.pgconn.encrypt_password(
                secrets[role].encode(), role.encode(), b"scram-sha-256"
            )
            conn.execute(
                sql.SQL("alter role {} password {}").format(
                    sql.Identifier(role), sql.Literal(hashed.decode())
                )
            )
    return applied


def ready_clients(platform_dsn: str) -> list[tuple[str, bytes | None]]:
    """(slug, sealed secrets) of every ready client in the registry."""
    with psycopg.connect(libpq(platform_dsn), autocommit=True) as conn:
        rows = conn.execute(
            "select slug, secrets from platform.clients where status = 'ready' order by slug"
        ).fetchall()
    return [(slug, bytes(box) if box else None) for slug, box in rows]


def apply_ready(clients: list[tuple[str, bytes | None]]) -> dict[str, list[str]]:
    """Bring every ready cloud client's vault up to date. The API runs this before each deploy
    (`.railway/railway.ts`), so no API version serves a vault without the migrations it reads.
    Clients registered by provision-local keep no admin password in the registry and are skipped
    (scripts/dev-up.sh migrates them). One client failing (its vault down, secrets that don't
    open) doesn't stop the others; then this raises, so the deploy stops and the running API
    stays."""
    from zm_api import secrets as sealed
    from zm_worker.provision.supabase import _dsn

    applied: dict[str, list[str]] = {}
    failed = []
    for slug, box in clients:
        try:
            secrets: dict[str, Any] = sealed.unseal(box) if box else {}
            if "supabase_ref" not in secrets:
                log.info("skipped: a local client, dev-up.sh migrates it", extra={"client": slug})
                continue
            dsn = _dsn(
                "postgres", secrets["supabase_ref"], secrets["db_password"], secrets["pooler_host"]
            )
            applied[slug] = apply(dsn, {role: secrets[f"{role}_password"] for role in ROLES})
        except Exception:
            log.exception("vault migrations failed", extra={"client": slug})
            failed.append(slug)
    if failed:
        raise RuntimeError(f"vault migrations failed for {', '.join(failed)}")
    return applied
