import hashlib
import logging
from pathlib import Path

import psycopg
from psycopg import sql

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
    # settings DSNs are SQLAlchemy URLs (postgresql+asyncpg://…); libpq wants plain postgresql://
    with psycopg.connect(dsn.replace("+asyncpg", "", 1), autocommit=True) as conn:
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
