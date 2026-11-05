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
        -- A client's requests go with the client (cascade), like its provisioning steps and
        -- releases index: nothing deletes a client yet, and submitted_by points into the client's
        -- own database, which goes too. Offboarding will decide what of a client to keep.
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
            idempotency_key    text,  -- the submit's Idempotency-Key header, if it sent one
            created_at         timestamptz not null default now(),
            updated_at         timestamptz not null default now(),
            check (payload ->> 'kind' = kind::text),
            unique (client_id, idempotency_key)
        )
    """,
    """
        -- a client's own list, newest first
        create index on platform.change_requests (client_id, created_at desc, id desc)
    """,
    """
        -- the owner console's queue (S2-24b): every client's requests by status, oldest first
        create index on platform.change_requests (status, created_at)
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
