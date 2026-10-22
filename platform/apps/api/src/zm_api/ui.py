"""`GET /app/ui` (#27): the active release's screens for the signed-in user.

The shell draws whatever this returns, so department filtering happens here, before the browser
sees anything (§16). A nav item is sent when its `visible_to` holds `"*"` or one of the user's
departments. Pages have no `visible_to` of their own: a page is sent when a nav item the user can
see points at it, or when no nav item points at it at all (`case` pages, pages only linked to).
Level-based rules come with #56.
"""

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zm_api import releases
from zm_api.context import ClientCtx, UserId, client_router
from zm_api.errors import NotFound, Unauthorized
from zm_api.releases import ReleaseRow
from zm_schema import WorkflowV1
from zm_schema.models import Apps, NavItem, Page


class UiClient(BaseModel):
    name: str
    theme: str
    """Theme preset key (D19), e.g. `quartz`."""
    accent: str | None
    """The client's default accent; null = the preset's own. A user's own choice wins."""


class UiRelease(BaseModel):
    version: int


class UiStage(BaseModel):
    key: str
    name: str
    department: str


class UiField(BaseModel):
    key: str
    label: str
    type: str


class UiFlow(BaseModel):
    key: str
    name: str
    case_label: str
    """What one case is called, e.g. "Site"."""
    case_code: str
    """The case code pattern, e.g. `MX-{seq:4}`: the shell finds a case's flow from its code."""
    stages: list[UiStage]
    start_fields: list[UiField]
    """The start form's fields in form order, so blocks can label and order case columns."""


class AppUi(BaseModel):
    client: UiClient
    release: UiRelease
    nav: list[NavItem]
    pages: list[Page]
    flows: list[UiFlow]


def visible_ui(client_name: str, release: ReleaseRow, departments: set[str]) -> AppUi:
    apps = Apps.model_validate(release.ui)
    wf = WorkflowV1.model_validate(release.workflow, context={"structural_only": True})
    nav = [n for n in apps.nav if "*" in n.visible_to or departments & set(n.visible_to)]
    shown, linked = {n.page for n in nav}, {n.page for n in apps.nav}
    flows = [
        UiFlow(
            key=f.key,
            name=f.name,
            case_label=f.case_label,
            case_code=f.case_code.pattern,
            stages=[UiStage(key=s.key, name=s.name, department=s.department) for s in f.stages],
            start_fields=[
                UiField(key=c.field, label=wf.fields[c.field].label, type=wf.fields[c.field].type)
                for section in wf.forms[f.start.form].sections
                for row in section.rows
                for c in row
            ],
        )
        for f in wf.flows
    ]
    return AppUi(
        client=UiClient(name=client_name, theme=apps.theme, accent=apps.accent),
        release=UiRelease(version=release.version),
        nav=nav,
        pages=[p for p in apps.pages if p.key in shown or p.key not in linked],
        flows=flows,
    )


async def departments(conn: AsyncConnection, uid: UUID) -> set[str]:
    """The user's departments; 401 when the user is gone or deactivated (as `/app/me`)."""
    rows = (
        await conn.execute(
            text(
                "select u.status, m.department from app.users u"
                " left join app.memberships m on m.user_id = u.id where u.id = :id"
            ),
            {"id": uid},
        )
    ).all()
    if not rows or rows[0].status != "active":
        raise Unauthorized()
    return {r.department for r in rows if r.department is not None}


routes = client_router()


@routes.get("/ui")
async def app_ui(ctx: ClientCtx, uid: UserId) -> AppUi:
    """The active release's nav, pages and flows, filtered to the user's departments."""
    async with ctx.db.connect() as conn:
        mine = await departments(conn, uid)
        release = await releases.active_release(conn)
    if release is None:
        raise NotFound("Nothing has been published yet")
    return visible_ui(ctx.client.name, release, mine)
