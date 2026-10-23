"""Client registry (S1-04): one row per client in platform.clients."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# `by` picks one of these fixed queries, so it never reaches the SQL as text.
# citext's case-insensitive `=` lives in schema `extensions`; when that schema is not on the
# search_path a plain `=` silently falls back to case-sensitive text equality, so name it.
_QUERIES = {
    "id": text("select * from platform.clients where id = :value"),
    "slug": text("select * from platform.clients where slug = :value"),
    "workspace_code": text(
        "select * from platform.clients where workspace_code"
        " operator(extensions.=) cast(:value as extensions.citext)"
    ),
}


@dataclass(frozen=True)
class Client:
    id: UUID
    slug: str
    name: str
    workspace_code: str
    template_key: str
    status: str
    supabase_ref: str | None
    operaton_url: str | None
    railway_service_id: str | None
    bucket: str | None
    secrets: bytes | None  # sealed; open with zm_api.secrets.unseal
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


async def get_client(
    conn: AsyncConnection,
    by: Literal["id", "slug", "workspace_code"],
    value: UUID | str,
) -> Client | None:
    """workspace_code matches case-insensitively (citext)."""
    row = (await conn.execute(_QUERIES[by], {"value": value})).mappings().one_or_none()
    return Client(**row) if row else None


async def ready_clients(conn: AsyncConnection) -> list[Client]:
    """Every client whose vault is ready, by slug (the worker's reconciler walks them)."""
    rows = await conn.execute(
        text("select * from platform.clients where status = 'ready' order by slug")
    )
    return [Client(**r) for r in rows.mappings()]
