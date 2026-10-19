"""Control-plane schema (S1-04, #17): clients, provisioning steps, owners, templates, releases.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# asyncpg runs one statement per execute, so each is its own string.
UPGRADE = [
    """
        create type platform.client_status as enum
            ('new', 'provisioning', 'ready', 'failed', 'suspended')
    """,
    """
        create type platform.step_status as enum
            ('pending', 'running', 'done', 'failed', 'needs_human')
    """,
    """
        create table platform.owners (
            id            uuid primary key default gen_random_uuid(),
            email         extensions.citext not null unique,
            name          text not null,
            password_hash text not null,
            status        text not null default 'active',
            created_at    timestamptz not null default now()
        )
    """,
    """
        create table platform.owner_refresh_tokens (
            id          uuid primary key default gen_random_uuid(),
            owner_id    uuid not null references platform.owners (id) on delete cascade,
            token_hash  text not null unique,
            family      uuid not null,
            expires_at  timestamptz not null,
            revoked_at  timestamptz
        )
    """,
    """
        create index on platform.owner_refresh_tokens (family)
    """,
    """
        create table platform.templates (
            key           text not null,
            version       int not null,
            name          text not null,
            workflow_json jsonb not null,
            primary key (key, version)
        )
    """,
    """
        create table platform.clients (
            id                 uuid primary key default gen_random_uuid(),
            slug               text not null unique,
            name               text not null,
            workspace_code     extensions.citext not null unique,
            template_key       text not null,
            status             platform.client_status not null default 'new',
            supabase_ref       text,
            operaton_url       text,
            railway_service_id text,
            bucket             text,
            -- Fernet-sealed JSON (zm_api / zm_engine DSNs, service account, service-role key)
            secrets            bytea,
            created_by         uuid references platform.owners (id),
            created_at         timestamptz not null default now(),
            updated_at         timestamptz not null default now()
        )
    """,
    """
        create table platform.provisioning_steps (
            client_id  uuid not null references platform.clients (id) on delete cascade,
            step       text not null,
            seq        int not null,
            status     platform.step_status not null default 'pending',
            attempts   int not null default 0,
            last_error text,
            updated_at timestamptz not null default now(),
            primary key (client_id, step)
        )
    """,
    """
        -- index only: the full release lives in the client's own database (S1-08b)
        create table platform.releases (
            client_id       uuid not null references platform.clients (id) on delete cascade,
            version         int not null,
            deployment_id   text not null,
            published_by    uuid references platform.owners (id),
            published_at    timestamptz not null default now(),
            workflow_sha256 text not null,
            primary key (client_id, version)
        )
    """,
]

DOWNGRADE = [
    "drop table platform.releases",
    "drop table platform.provisioning_steps",
    "drop table platform.clients",
    "drop table platform.templates",
    "drop table platform.owner_refresh_tokens",
    "drop table platform.owners",
    "drop type platform.step_status",
    "drop type platform.client_status",
]


def upgrade() -> None:
    # Supabase keeps extensions in schema `extensions`; plain Postgres (pg-platform) gets it here.
    op.execute("create schema if not exists extensions")
    op.execute("create extension if not exists citext with schema extensions")

    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
    # citext and schema `extensions` stay: on Supabase they belong to the platform, not to us.
