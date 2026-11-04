"""Change requests (S2-24a, #45): what a client's admin asks us to change, until it ships.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# asyncpg runs one statement per execute, so each is its own string.
UPGRADE = [
    """
        create type platform.change_request_kind as enum ('add_field', 'rename_field_label',
            'add_document_type', 'set_required', 'add_select_option')
    """,
    """
        create type platform.change_request_status as enum
            ('submitted', 'in_review', 'approved', 'rejected', 'shipped')
    """,
    """
        create table platform.change_requests (
            id                 uuid primary key default gen_random_uuid(),
            client_id          uuid not null references platform.clients (id) on delete cascade,
            submitted_by       uuid not null,  -- app.users.id in the client's own database
            kind               platform.change_request_kind not null,
            payload            jsonb not null,  -- zm_api.change_requests.Payload, kind included
            note               text,
            status             platform.change_request_status not null default 'submitted',
            review_note        text,
            reviewed_by        uuid references platform.owners (id),
            shipped_in_version int,
            created_at         timestamptz not null default now(),
            updated_at         timestamptz not null default now()
        )
    """,
    """
        -- a client's own list, newest first
        create index on platform.change_requests (client_id, created_at desc)
    """,
]

DOWNGRADE = [
    "drop table platform.change_requests",
    "drop type platform.change_request_status",
    "drop type platform.change_request_kind",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
