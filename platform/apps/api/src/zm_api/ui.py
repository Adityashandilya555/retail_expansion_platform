"""`GET /app/ui` (#27): the active release's screens for the signed-in user.

The shell draws whatever this returns, so department filtering happens here, before the browser
sees anything (§16). A nav item is sent when its `visible_to` holds `"*"` or one of the user's
departments. Pages have no `visible_to` of their own: a page is sent when a nav item the user can
see points at it, or when no nav item points at it at all (`case` pages, pages only linked to).
Level-based rules come with #56.

Each flow carries its start form (#170) when `tasks.start_case` would let the user start it (the
same `tasks.startable_flow` rule), as it validates it; it is read from the release's forms, so a
release published before #170 gets it too.
"""

from collections.abc import Mapping

from pydantic import BaseModel

from zm_api import preview, releases, tasks
from zm_api.context import ClientCtx, Preview, Viewer, client_router
from zm_api.errors import Forbidden, NotFound
from zm_api.releases import ReleaseRow
from zm_api.tasks_routes import FormOut
from zm_schema import WorkflowV1, group_id
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
    sla_days: int | None = None
    """Days a case may spend in the stage before it shows as overdue (S2-29)."""


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
    start_form: FormOut | None
    """The start form as `POST /app/cases` validates it for this user, without sections hidden
    from their level; null when they may not start the flow."""


class AppUi(BaseModel):
    client: UiClient
    release: UiRelease
    nav: list[NavItem]
    pages: list[Page]
    flows: list[UiFlow]


def form_fields(wf: WorkflowV1, form: str) -> list[UiField]:
    """A form's fields in form order."""
    return [
        UiField(key=c.field, label=wf.fields[c.field].label, type=wf.fields[c.field].type)
        for section in wf.forms[form].sections
        for row in section.rows
        for c in row
    ]


def start_form(release: ReleaseRow, flow_key: str, levels: Mapping[str, int]) -> FormOut | None:
    """The flow's start form as this user fills it; None when `POST /app/cases` would refuse
    them (`tasks.startable_flow`) or the release has no start form for the flow."""
    try:
        tasks.startable_flow(release, flow_key, {group_id(d, n) for d, n in levels.items()})
    except (NotFound, Forbidden):
        return None
    form = release.forms.get(f"start:{flow_key}")
    return None if form is None else FormOut(**tasks.visible_form(form, levels))


def visible_ui(client_name: str, release: ReleaseRow, levels: Mapping[str, int]) -> AppUi:
    """`levels`: department → the user's level in it (`tasks.person`)."""
    apps = Apps.model_validate(release.ui)
    wf = WorkflowV1.model_validate(release.workflow, context={"structural_only": True})
    nav = [n for n in apps.nav if "*" in n.visible_to or levels.keys() & set(n.visible_to)]
    shown, linked = {n.page for n in nav}, {n.page for n in apps.nav}
    flows = [
        UiFlow(
            key=f.key,
            name=f.name,
            case_label=f.case_label,
            case_code=f.case_code.pattern,
            stages=[
                UiStage(key=s.key, name=s.name, department=s.department, sla_days=s.sla_days)
                for s in f.stages
            ],
            start_fields=form_fields(wf, f.start.form),
            start_form=start_form(release, f.key, levels),
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


routes = client_router()


@routes.get("/ui")
async def app_ui(ctx: ClientCtx, uid: Viewer) -> AppUi:
    """The active release's nav, pages and flows, filtered to the user's departments, with the
    start forms of the flows they may start. Under a preview token (#42): the draft's, as
    publishing it now would make them, for the role. 401 once the user is deactivated."""
    if isinstance(uid, Preview):
        draft = await preview.draft_release(ctx)
        return visible_ui(ctx.client.name, draft, {uid.department: uid.level})
    async with ctx.db.connect() as conn:
        me = await tasks.person(conn, uid)
        release = await releases.active_release(conn)
    if release is None:
        raise NotFound("Nothing has been published yet")
    return visible_ui(ctx.client.name, release, me.levels)
