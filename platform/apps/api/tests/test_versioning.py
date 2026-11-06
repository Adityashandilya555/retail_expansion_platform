"""Field versioning (#35, S2-25): fields and document types change additively, by key.

Unit: the rules (`zm_api.versioning`) against the published releases, the draft's write path and
publish with them, the agent's field tools under them, publish's stamps and the site page's
sections. Integration (`pytest -m integration` after dev-up.sh): demo 2 on the pilot vault,
published twice, then v1 made active again.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from zm_api import cases, drafts, publish, releases, tasks, versioning
from zm_api.agent import tools
from zm_api.errors import Conflict, UnprocessableContent
from zm_api.main import create_app
from zm_api.releases import cache_clear
from zm_api.users import seed_users
from zm_compiler import compile
from zm_schema import Error, migrate

from .agent.test_tools import agent, held, sent  # noqa: F401 (held: the draft kept in memory)
from .test_cases_integration import DOMAIN, World, clear, engine_client, env, env_vars  # noqa: F401
from .test_draft import live
from .test_publish import INI, platform_sql, register, vault_sql
from .test_publish_unit import matrix_retail
from .test_tasks import Stack

Doc = dict[str, Any]
OWNER = uuid.uuid4()
TEMPLATES = Path(__file__).resolve().parents[3] / "packages" / "templates"
OPTIONAL = json.loads((TEMPLATES / "matrix-retail" / "optional-fields.json").read_text())["fields"]
LIVE = live(matrix_retail())  # v1, as publish records it
V1 = [LIVE["fields"]]  # the published releases' fields once v1 is out
TYPE_CHANGE = [{"op": "replace", "path": "/fields/carpet_area/type", "value": "text"}]
STORE_MODELS = matrix_retail()["fields"]["store_model"]["options"]
SITE = ["site_name", "city", "address", "store_model"]

# Demo 2's edits, as the agent's tools (#36) make them: state and district from the optional
# catalogue on the start form, city labelled "Town", address removed, site photos made required
# and a store model added.
DEMO_2: list[tuple[str, Doc]] = [
    *[
        ("add_field", {"key": k, **OPTIONAL[k], "required": False, "options": []})
        for k in ("state", "district")
    ],
    (
        "arrange_form",
        {
            "form": "start",
            "sections": [
                {
                    "key": "site",
                    "title": "Site",
                    "hidden_below_level": None,
                    "rows": [
                        *([{"field": k, "span": 12}] for k in SITE),
                        [{"field": "state", "span": 6}, {"field": "district", "span": 6}],
                    ],
                }
            ],
        },
    ),
    ("update_field", {"key": "city", "label": "Town", "options": [], "required": True}),
    ("remove_field", {"key": "address"}),
    (
        "update_field",
        {"key": "site_photos", "label": "Site photos / deck", "options": [], "required": True},
    ),
    (
        "update_field",
        {
            "key": "store_model",
            "label": "Store model",
            "options": [*STORE_MODELS, {"id": "drive_thru", "label": "Drive-thru"}],
            "required": True,
        },
    ),
]


def demo_2() -> Doc:
    """Matrix Retail after DEMO_2, from the tools' own patches, each checked against v1."""
    doc = matrix_retail()
    for name, args in DEMO_2:
        t = tools.TOOLS[name]
        assert t.edit is not None
        doc, _ = drafts.patched(doc, t.edit(doc, t.args.model_validate(sent(name, args))), V1)
    return doc


def without(doc: Doc, key: str) -> Doc:
    return {**doc, "fields": {k: f for k, f in doc["fields"].items() if k != key}}


# --- unit: the rules -------------------------------------------------------------------------


def test_adding_relabelling_hiding_and_requiring_fields_is_allowed() -> None:
    doc = demo_2()
    assert versioning.errors(V1, doc) == []
    assert doc["fields"]["city"]["label"] == "Town" and "town" not in doc["fields"]
    assert doc["fields"]["address"]["hidden"] is True
    assert {"state", "district"} <= doc["fields"].keys()
    doc["fields"]["store_model"]["options"].pop(0)  # stored values keep their raw id
    doc["fields"]["site_photos"]["document"]["accept"] = ["pdf"]  # for new uploads
    assert versioning.errors(V1, doc) == []


def test_a_type_change_needs_a_migration_decision() -> None:
    doc = matrix_retail()
    doc["fields"]["carpet_area"]["type"] = "text"
    assert versioning.errors(V1, doc) == [
        Error(
            "fields.carpet_area.type",
            "carpet_area is a number in a published release: changing its type needs a migration"
            " decision; add a new field instead",
        )
    ]
    assert versioning.errors([], doc) == []  # nothing published yet: no rules
    # a type changed before these rules existed: the newest release that has the key decides
    legacy = [{"carpet_area": {**LIVE["fields"]["carpet_area"], "type": "text"}}, *V1]
    assert versioning.errors(legacy, matrix_retail()) == []
    assert [e.path for e in versioning.errors(legacy, doc)] == ["fields.carpet_area.type"]


def test_a_published_field_is_hidden_never_deleted() -> None:
    assert versioning.errors(V1, without(matrix_retail(), "dd_notes")) == [
        Error(
            "fields.dd_notes",
            "dd_notes is in a published release: hide it instead of deleting it, so its stored"
            " values stay readable",
        )
    ]


def test_after_a_rollback_a_newer_releases_fields_still_hold() -> None:
    """v1 reactivated after v2 added state and district: they are still published, so a draft
    can't delete or retype them, whichever release is active; a new field is still fine."""
    published = [*V1, live(demo_2())["fields"]]
    assert [e.path for e in versioning.errors(published, matrix_retail())] == [
        "fields.state",
        "fields.district",
    ]
    retype = [{"op": "replace", "path": "/fields/state/type", "value": "number"}]
    with pytest.raises(UnprocessableContent) as e:
        drafts.patched(demo_2(), retype, published)
    assert [x.path for x in e.value.errors or []] == ["fields.state.type"]
    gst = [{"op": "add", "path": "/fields/gst_no", "value": OPTIONAL["gst_no"]}]
    assert "gst_no" in drafts.patched(demo_2(), gst, published)[0]["fields"]


def test_a_type_change_is_refused_and_the_draft_is_unchanged() -> None:
    doc = matrix_retail()
    with pytest.raises(UnprocessableContent) as e:
        drafts.patched(doc, TYPE_CHANGE, V1)
    assert [x.path for x in e.value.errors or []] == ["fields.carpet_area.type"]
    assert "needs a migration decision" in (e.value.errors or [])[0].message
    assert doc == matrix_retail()
    drafts.patched(doc, TYPE_CHANGE)  # before the first publish it is just an edit


def test_an_old_violation_doesnt_block_an_unrelated_change() -> None:
    """As with schema problems: a draft written outside the log (seed-draft, SQL) still takes a
    change that adds no problem, and publish reports the old one."""
    broken, _ = drafts.patched(matrix_retail(), TYPE_CHANGE)
    after, _ = drafts.patched(
        broken, [{"op": "replace", "path": "/apps/accent", "value": "copper"}], V1
    )
    assert after["apps"]["accent"] == "copper"
    with pytest.raises(UnprocessableContent) as e:
        drafts.patched(broken, [{"op": "remove", "path": "/fields/dd_notes"}], V1)
    assert "fields.dd_notes" in [x.path for x in e.value.errors or []]


def test_the_write_path_and_validate_check_the_published_releases(
    held: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def v1(_conn: Any) -> list[Doc]:
        return V1

    monkeypatch.setattr(releases, "published_fields", v1)
    results = asyncio.run(agent(held.ctx, OWNER, [(t, sent(t, a)) for t, a in DEMO_2]))
    assert all(r["ok"] for r in results), results
    assert held.doc == demo_2()
    logged = len(held.log)
    with pytest.raises(UnprocessableContent):
        asyncio.run(drafts.apply_change(held.ctx, OWNER, "test", TYPE_CHANGE, "Carpet area: text"))
    assert len(held.log) == logged
    held.doc, _ = drafts.patched(held.doc, TYPE_CHANGE)  # written outside the log
    problems = asyncio.run(drafts.validate_draft(held.ctx))
    assert [e.path for e in problems] == ["fields.carpet_area.type"]


class Newest:
    """`held`'s vault for undo as well: its query finds the newest logged change, still current."""

    def __init__(self, log: list[Doc]) -> None:
        self.log = log

    @asynccontextmanager
    async def begin(self) -> AsyncIterator["Newest"]:
        yield self

    async def execute(self, _sql: Any) -> Any:
        row = {**self.log[-1], "id": len(self.log), "current": True}
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row))


def test_undo_cant_delete_a_field_published_since(
    held: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add a field, publish it, undo: 409, and the draft and its log are unchanged. Unpublished,
    the same undo goes through."""
    published: list[Doc] = []

    async def fields(_conn: Any) -> list[Doc]:
        return published

    monkeypatch.setattr(releases, "published_fields", fields)
    held.ctx.db = Newest(held.log)
    gst = [{"op": "add", "path": "/fields/gst_no", "value": OPTIONAL["gst_no"]}]
    asyncio.run(drafts.apply_change(held.ctx, OWNER, "add_field", gst, "Added GST no"))
    added, logged = held.doc, list(held.log)
    published.append(live(added)["fields"])  # v1
    with pytest.raises(Conflict) as e:
        asyncio.run(drafts.undo(held.ctx, OWNER))
    assert "gst_no is in a published release: hide it instead" in (e.value.detail or "")
    assert held.doc == added and held.log == logged
    published.clear()
    asyncio.run(drafts.undo(held.ctx, OWNER))
    assert held.doc == matrix_retail()


def test_publish_refuses_what_the_rules_refuse() -> None:
    broken, _ = drafts.patched(matrix_retail(), TYPE_CHANGE)
    with pytest.raises(UnprocessableContent, match="can't be published") as e:
        publish._compile("pilot-local", broken, V1)
    assert [x.path for x in e.value.errors or []] == ["fields.carpet_area.type"]
    publish._compile("pilot-local", broken)  # the first release is the baseline


def test_publish_stamps_what_a_release_adds_or_makes_required() -> None:
    first = live(matrix_retail())
    first["fields"]["state"] = {**OPTIONAL["state"], "since": 2}  # e.g. from a template
    first["fields"]["city"]["required_from_version"] = 2
    versioning.stamp([], first, 1)
    assert without(first, "state") == LIVE  # the first release is the baseline: nothing stamped
    assert first["fields"]["state"] == OPTIONAL["state"]
    v2 = live(demo_2())
    v2["fields"]["city"]["since"] = 9  # whatever a draft says, publish decides
    versioning.stamp(V1, v2, 2)
    f = v2["fields"]
    assert (f["state"]["since"], f["district"]["since"]) == (2, 2)
    assert f["site_photos"]["required_from_version"] == 2
    assert "since" not in f["city"] and "required_from_version" not in f["city"]  # from the start
    assert "required_from_version" not in f["state"]
    v3 = live(demo_2())
    v3["fields"]["site_photos"]["required"] = False
    versioning.stamp([*V1, v2["fields"]], v3, 3)  # whichever is active: v1 after a rollback
    assert v3["fields"]["state"]["since"] == 2  # kept from the release that added it
    assert "required_from_version" not in v3["fields"]["site_photos"]


def test_the_diff_ignores_publishs_stamps() -> None:
    v2 = live(demo_2())
    versioning.stamp(V1, v2, 2)
    assert drafts.diff(v2, demo_2()).fields.changed == []


# --- unit: the site page ---------------------------------------------------------------------


def test_a_v1_site_reads_on_v2s_start_form() -> None:
    v2 = live(demo_2())
    versioning.stamp(V1, v2, 2)
    spec = compile(migrate(demo_2()), "pilot-local").forms["start:store_launch"]
    form = tasks.visible_form({"schema": spec.schema, "layout": spec.layout}, {"bd": 1})
    assert "address" not in spec.schema["properties"]  # removed: not on new forms
    old = {
        "site_name": "Indiranagar",
        "city": "Bengaluru",
        "address": "12th Main",
        "store_model": "kiosk",
    }
    [site] = cases.sections(form, old, v2["fields"], LIVE["fields"])
    assert [(f["key"], f["label"], f["value"], f["since"]) for f in site.fields] == [
        ("site_name", "Site name", "Indiranagar", None),
        ("city", "Town", "Bengaluru", None),
        ("address", "Address", "12th Main", None),  # hidden since, its value still reads
        ("store_model", "Store model", "kiosk", None),
        ("state", "State", None, 2),
        ("district", "District", None, 2),
    ]
    new = {**old, "state": "Karnataka", "district": "Bengaluru Urban"}
    del new["address"]
    [site] = cases.sections(form, new, v2["fields"], v2["fields"])
    assert [(f["key"], f["value"], f["since"]) for f in site.fields] == [
        ("site_name", "Indiranagar", None),
        ("city", "Bengaluru", None),
        ("store_model", "kiosk", None),
        ("state", "Karnataka", None),
        ("district", "Bengaluru Urban", None),
    ]


def test_a_value_the_form_no_longer_places_still_shows_last() -> None:
    """arrange_form dropped address from v2's start form: a v1 site's address shows in a last
    section, under v2's label."""
    v2 = live(demo_2())
    v2["fields"]["address"]["label"] = "Street"
    spec = compile(migrate(demo_2()), "pilot-local").forms["start:store_launch"]
    rows = [r for r in spec.layout[0]["rows"] if r[0]["field"] != "address"]
    form = {"schema": spec.schema, "layout": [{**spec.layout[0], "rows": rows}]}
    old = {"site_name": "Indiranagar", "address": "12th Main"}
    site, other = cases.sections(form, old, v2["fields"], LIVE["fields"])
    assert "address" not in [f["key"] for f in site.fields]
    assert (other.key, other.title) == ("_other", "Other details")
    assert other.fields == [
        {
            "key": "address",
            "label": "Street",
            "type": "textarea",
            "value": "12th Main",
            "since": None,
        }
    ]
    assert len(cases.sections(form, {"site_name": "Indiranagar"}, v2["fields"], v2["fields"])) == 1


# --- integration: demo 2 on the pilot vault ----------------------------------------------------

START = {
    "site_name": "Indiranagar",
    "city": "Bengaluru",
    "address": "12th Main",
    "store_model": "kiosk",
}
DETAILS = {
    "carpet_area": 400,
    "frontage_ft": 20,
    "monthly_rent": 90000,
    "nearest_competitor_m": 300,
}


def _draft() -> Doc:
    return vault_sql("pilot", "select workflow from app.drafts where id = 1")[0][0]


def _start(w: World, values: Doc) -> str:
    response = w.http.post(
        "/app/cases", json={"flow": "store_launch", "values": values}, headers=w.auth("bd2")
    )
    assert response.status_code == 201, response.text
    return response.json()["code"]


def _submit_details(w: World, case: str) -> httpx.Response:
    """bd_site_details without site photos, which v2 made required."""
    [task] = [
        t
        for t in w.get("bd2", "/app/tasks", scope="mine", case=case)
        if t["form_key"] == "zm:bd_site_details"
    ]
    headers = {**w.auth("bd2"), "Idempotency-Key": str(uuid.uuid4())}
    return w.http.post(f"/app/tasks/{task['id']}/submit", json={"values": DETAILS}, headers=headers)


def _story(http: TestClient, slug: str) -> SimpleNamespace:
    def step(go: Callable[..., Any]) -> Any:
        async def run() -> Any:
            async with Stack() as stack:
                return await go(await stack.ctx(slug), stack)

        return asyncio.run(run())

    def publish_draft() -> int:
        return step(lambda ctx, s: publish.publish(ctx, s.registry.engine(), None)).version

    assert publish_draft() == 1
    bd2 = {"email": f"bd2@{DOMAIN}", "name": "Bd2", "department": "bd", "level": 2}
    [seeded] = step(lambda ctx, _: seed_users(ctx, [bd2]))
    w = World(http, slug, {"bd2": seeded.id}, {})
    old = _start(w, START)  # MX-0001 on v1, with city

    before = _draft()
    with pytest.raises(UnprocessableContent) as refused:
        step(lambda ctx, _: drafts.apply_change(ctx, OWNER, "test", TYPE_CHANGE, "Carpet area"))
    unchanged = _draft()

    results = step(lambda ctx, _: agent(ctx, OWNER, [(t, sent(t, a)) for t, a in DEMO_2]))
    assert all(r["ok"] for r in results), results
    assert publish_draft() == 2
    v2_start = {k: v for k, v in START.items() if k != "address"}  # not on v2's form
    new = _start(w, {**v2_start, "state": "Karnataka", "district": "Bengaluru Urban"})
    return SimpleNamespace(
        step=step,
        w=w,
        old=old,
        new=new,
        refused=refused.value,
        drafts=(before, unchanged),
        submitted={code: _submit_details(w, code) for code in (old, new)},
    )


@pytest.fixture(scope="module")
def demo() -> Iterator[SimpleNamespace]:
    with pytest.MonkeyPatch.context() as mp:
        for k, v in env_vars().items():
            mp.setenv(k, v)
        cfg = Config(str(INI))
        cfg.cmd_opts = argparse.Namespace(x=["db=platform"])
        command.upgrade(cfg, "head")
        clear()
        cache_clear()
        client_id, slug = asyncio.run(register("pilot", "pilot"))
        vault_sql(
            "pilot",
            "insert into app.drafts (id, workflow, template_key) values (1, %s, 'matrix-retail')",
            json.dumps(matrix_retail()),
        )
        try:
            with TestClient(create_app()) as http:
                yield _story(http, slug)
        finally:
            asyncio.run(platform_sql("delete from platform.clients where id = :i", i=client_id))
            clear()
            cache_clear()
            vault_sql("pilot", "truncate app.draft_changes")
            with engine_client("pilot") as op:
                for d in op.get("deployment", params={"name": f"{slug}-release"}).json():
                    op.delete(f"deployment/{d['id']}", params={"cascade": "true"})


def _fields(page: Doc) -> dict[str, tuple[str, Any, int | None]]:
    return {
        f["key"]: (f["label"], f["value"], f["since"])
        for s in page["sections"]
        for f in s["fields"]
    }


def _release(version: int) -> Doc:
    return vault_sql("pilot", "select workflow from app.releases where version = %s", version)[0][0]


@pytest.mark.integration
def test_a_v1_site_shows_city_and_the_fields_v2_added_as_added_in_v2(demo: SimpleNamespace) -> None:
    old = demo.w.get("bd2", f"/app/cases/{demo.old}")
    assert old["release_version"] == 1
    fields = _fields(old)
    assert fields["city"] == ("Town", "Bengaluru", None)
    assert fields["state"] == ("State", None, 2) and fields["district"] == ("District", None, 2)
    new = demo.w.get("bd2", f"/app/cases/{demo.new}")
    assert new["release_version"] == 2
    assert _fields(new)["state"] == ("State", "Karnataka", None)
    assert (
        _release(2)["fields"]["state"]["since"],
        _release(2)["fields"]["district"]["since"],
    ) == (2, 2)
    # every recorded value keeps the release it was captured on
    assert vault_sql(
        "pilot",
        "select distinct c.code, v.release_version from app.case_values v"
        " join app.cases c on c.id = v.case_id order by 1",
    ) == [(demo.old, 1), (demo.new, 2)]


@pytest.mark.integration
def test_a_renamed_field_keeps_its_key_and_old_values_show_under_the_new_label(
    demo: SimpleNamespace,
) -> None:
    v2 = _release(2)["fields"]
    assert v2["city"]["label"] == "Town" and "town" not in v2
    assert _fields(demo.w.get("bd2", f"/app/cases/{demo.old}"))["city"][:2] == ("Town", "Bengaluru")


@pytest.mark.integration
def test_a_type_change_is_refused_with_a_clear_message_and_changes_nothing(
    demo: SimpleNamespace,
) -> None:
    [error] = demo.refused.errors
    assert error.path == "fields.carpet_area.type" and "needs a migration decision" in error.message
    before, unchanged = demo.drafts
    assert unchanged == before


@pytest.mark.integration
def test_a_removed_field_leaves_new_forms_and_old_values_stay_readable(
    demo: SimpleNamespace,
) -> None:
    [(on_v2_form,)] = vault_sql(
        "pilot",
        "select forms->'start:store_launch'->'schema'->'properties' ? 'address' from app.releases"
        " where version = 2",
    )
    assert on_v2_form is False
    assert _fields(demo.w.get("bd2", f"/app/cases/{demo.old}"))["address"][1] == "12th Main"
    assert "address" not in _fields(demo.w.get("bd2", f"/app/cases/{demo.new}"))


@pytest.mark.integration
def test_a_field_required_in_v2_binds_v2_sites_only(demo: SimpleNamespace) -> None:
    assert demo.submitted[demo.old].status_code == 200, demo.submitted[demo.old].text
    refused = demo.submitted[demo.new]
    assert refused.status_code == 422
    assert refused.json()["errors"] == [{"path": "/site_photos", "message": "Required"}]
    assert _release(2)["fields"]["site_photos"]["required_from_version"] == 2


@pytest.mark.integration
def test_after_a_rollback_to_v1_a_v2_site_and_v2s_fields_are_kept(
    demo: SimpleNamespace,
) -> None:
    """v1 made active again (`releases.activate`, publish's rollback path): a site started on v2
    still reads on v2's start form, and the draft still can't retype a field v2 added."""

    def activate(version: int) -> None:
        async def go(ctx: Any, _: Any) -> None:
            async with ctx.db.begin() as conn:
                await releases.activate(conn, version)

        demo.step(go)

    activate(1)
    try:
        state = _fields(demo.w.get("bd2", f"/app/cases/{demo.new}"))["state"]
        assert state == ("State", "Karnataka", None)
        retype = [{"op": "replace", "path": "/fields/state/type", "value": "number"}]
        with pytest.raises(UnprocessableContent) as refused:
            demo.step(lambda ctx, _: drafts.apply_change(ctx, OWNER, "test", retype, "State"))
        assert [e.path for e in refused.value.errors or []] == ["fields.state.type"]
    finally:
        activate(2)
