"""Draft + change log (#33): patch, undo and diff logic as unit tests; the log, the endpoints and
the insert-only trigger against the pilot vault (`pytest -m integration` after dev-up.sh)."""

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import jsonpatch
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from zm_api import drafts, releases
from zm_api.context import Resolver
from zm_api.errors import UnprocessableContent
from zm_api.main import create_app
from zm_api.registry import Client
from zm_schema import migrate

from .conftest import bearer, token
from .test_auth import PILOT_DSN
from .test_publish import JWT_SECRET, Clients, Stack, clients, vault_sql  # noqa: F401
from .test_publish_unit import FakePlatform, FakeRegistry, matrix_retail

CHANGES = (  # three edits an agent might make, each valid on its own
    [{"op": "replace", "path": "/fields/site_name/label", "value": "Site name (as on the lease)"}],
    [{"op": "add", "path": "/fields/gst_no", "value": {"type": "text", "label": "GST no"}}],
    [{"op": "replace", "path": "/apps/accent", "value": "copper"}],
)


def canonical(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True)


# --- unit: patch, inverse, diff ----------------------------------------------------------------


def test_undoing_two_of_three_changes_gives_back_the_first() -> None:
    states, inverses = [matrix_retail()], []
    for patch in CHANGES:
        after, inverse = drafts.patched(states[-1], patch)
        states.append(after)
        inverses.append(inverse)
    doc = states[-1]
    for inverse in reversed(inverses[1:]):
        doc, _ = drafts.patched(doc, inverse)
    assert canonical(doc) == canonical(states[1])
    assert states[0] == matrix_retail()  # patching never touches its input


def test_a_patch_that_breaks_validation_is_422_with_paths() -> None:
    doc = matrix_retail()
    with pytest.raises(UnprocessableContent) as e:
        drafts.patched(doc, [{"op": "add", "path": "/flows/0/stages/0/after/-", "value": "nope"}])
    assert [(x.path, x.message) for x in e.value.errors or []] == [
        ("flows[0].stages[0].after[0]", "unknown stage 'nope' in flow 'store_launch'")
    ]
    assert doc == matrix_retail()


def test_an_invalid_draft_takes_a_change_that_adds_no_problem_and_refuses_one_that_does() -> None:
    broken = matrix_retail()  # e.g. as an undo restored it, without validating it again
    broken["flows"][0]["stages"][0]["after"] = ["nope"]
    after, _ = drafts.patched(broken, CHANGES[1])  # unrelated: the old problem stays
    assert "gst_no" in after["fields"] and len(drafts.validate(after)) == 1
    with pytest.raises(UnprocessableContent) as e:
        drafts.patched(broken, [{"op": "add", "path": "/flows/0/stages/1/after/-", "value": "x"}])
    assert [(x.path, x.message) for x in e.value.errors or []] == [
        ("flows[0].stages[1].after[1]", "unknown stage 'x' in flow 'store_launch'")
    ]  # only what the change brings in


def test_a_change_that_moves_stages_or_tasks_on_an_invalid_draft_adds_no_problem() -> None:
    """Old problems' paths move with their list items; they still aren't the change's fault."""
    broken = matrix_retail()
    broken["flows"][0]["stages"][1]["after"] = ["nope"]  # reported at stages[1]
    moved, _ = drafts.patched(  # now at stages[0]: the same problem, not a new one
        broken, [{"op": "move", "from": "/flows/0/stages/1", "path": "/flows/0/stages/0"}]
    )
    problems = sorted(drafts._problem(e) for e in drafts.validate(broken))
    assert len(problems) > 1  # `after` and the `show`s that lost their earlier stage
    assert sorted(drafts._problem(e) for e in drafts.validate(moved)) == problems
    assert "flows[0].stages[0].after[0]" in [e.path for e in drafts.validate(moved)]
    intro = {"id": "bd_loi_intro", "name": "Intro", "performer": {"department": "bd", "level": 1}}
    shifted, _ = drafts.patched(  # a task first in the broken stage: its old problems shift down
        broken, [{"op": "add", "path": "/flows/0/stages/1/tasks/0", "value": intro}]
    )
    assert sorted(drafts._problem(e) for e in drafts.validate(shifted)) == problems
    with pytest.raises(UnprocessableContent):  # reordering that breaks a `show` is still refused
        tasks = "/flows/0/stages/1/tasks"
        drafts.patched(broken, [{"op": "move", "from": f"{tasks}/1", "path": f"{tasks}/0"}])
    with pytest.raises(UnprocessableContent) as e:  # a second, new problem of the same kind
        drafts.patched(
            broken, [{"op": "add", "path": "/flows/0/stages/2/after/-", "value": "nope"}]
        )
    assert [x.path for x in e.value.errors or []] == ["flows[0].stages[2].after[1]"]


@pytest.mark.parametrize(
    "patch",
    [
        [{"op": "remove", "path": "/fields/no_such_field"}],
        [{"op": "move", "from": 3, "path": "/apps"}],  # a TypeError inside jsonpatch, not a 500
    ],
)
def test_a_patch_that_doesnt_apply_is_422(patch: list[dict[str, Any]]) -> None:
    with pytest.raises(UnprocessableContent) as e:
        drafts.patched(matrix_retail(), patch)
    assert [x.path for x in e.value.errors or []] == ["$"]


def test_an_inverse_that_jsonpatch_gets_wrong_replaces_the_whole_document() -> None:
    before, after = {"x": [{"e": "a"}, ["a", "b"], ["b"]]}, {"x": ["b", []]}
    inverse = drafts.inverse(before, after)
    assert inverse == [{"op": "replace", "path": "", "value": before}]  # make_patch's list moves
    assert jsonpatch.apply_patch(after, inverse) == before
    small = drafts.inverse({"a": 1}, {"a": 2})
    assert small == [{"op": "replace", "path": "/a", "value": 1}]  # usually just the difference


def live(doc: dict[str, Any]) -> dict[str, Any]:
    """The workflow as publish records it (migrated, unset defaults left out)."""
    return migrate(doc).model_dump(mode="json", by_alias=True, exclude_unset=True)


def test_no_difference_between_draft_and_live_is_an_empty_diff() -> None:
    assert drafts.diff(live(matrix_retail()), matrix_retail()).model_dump() == {
        "stages": {"added": [], "removed": [], "changed": []},
        "tasks": {"added": [], "removed": [], "changed": []},
        "fields": {"added": [], "removed": [], "changed": []},
        "blocks": [],
        "theme": None,
        "accent": None,
    }


def test_one_added_field_is_the_whole_diff() -> None:
    draft, _ = drafts.patched(matrix_retail(), CHANGES[1])
    found = drafts.diff(live(matrix_retail()), draft)
    assert found.fields.model_dump() == {"added": ["gst_no"], "removed": [], "changed": []}
    assert found.model_dump(exclude={"fields"}) == drafts.diff(
        live(matrix_retail()), matrix_retail()
    ).model_dump(exclude={"fields"})


def test_the_diff_names_changed_tasks_blocks_and_the_look() -> None:
    draft = matrix_retail()
    draft["flows"][0]["stages"][0]["tasks"][0]["name"] = "Review the draft site"
    del draft["flows"][0]["stages"][0]["tasks"][2]
    draft["apps"]["pages"][0]["regions"]["main"].pop()
    draft["apps"]["theme"], draft["apps"]["accent"] = "warm", None
    found = drafts.diff(live(matrix_retail()), draft)
    assert found.tasks.model_dump() == {
        "added": [],
        "removed": ["bd_approve_details"],
        "changed": ["bd_review_draft"],
    }
    assert found.blocks == ["home"]
    assert found.theme is not None and (found.theme.live, found.theme.draft) == ("quartz", "warm")
    assert found.accent is not None and (found.accent.live, found.accent.draft) == ("slate", None)


def test_a_task_moved_to_another_stage_is_changed() -> None:
    draft = matrix_retail()
    stages = draft["flows"][0]["stages"]
    stages[0]["tasks"].append(stages[1]["tasks"].pop())  # bd_send_legal: bd_loi → bd_qualification
    found = drafts.diff(live(matrix_retail()), draft)
    assert found.tasks.model_dump() == {"added": [], "removed": [], "changed": ["bd_send_legal"]}
    assert found.stages.changed == ["store_launch.bd_loi", "store_launch.bd_qualification"]


def test_a_stage_whose_after_changed_is_changed() -> None:
    draft = matrix_retail()
    draft["flows"][0]["stages"][4]["after"] = ["legal"]  # design no longer waits for finance
    found = drafts.diff(live(matrix_retail()), draft)
    assert found.stages.model_dump() == {
        "added": [],
        "removed": [],
        "changed": ["store_launch.design"],
    }
    assert found.model_dump(exclude={"stages"}) == drafts.diff(
        live(matrix_retail()), matrix_retail()
    ).model_dump(exclude={"stages"})


def test_before_the_first_publish_everything_is_added() -> None:
    found = drafts.diff(None, matrix_retail())
    assert "bd_review_draft" in found.tasks.added and "site_name" in found.fields.added
    assert "store_launch.bd_qualification" in found.stages.added
    assert found.theme is not None and found.theme.live is None


# --- unit: endpoint guards ---------------------------------------------------------------------

ROUTES = (
    ("get", ""),
    ("get", "/changes"),
    ("post", "/undo"),
    ("get", "/diff"),
    ("get", "/validate"),
    ("patch", "/theme"),
)


@pytest.fixture
def api(rows: dict[str, Client], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def get_client(_conn: Any, by: str, value: Any) -> Client | None:
        assert by == "id"
        return next((row for row in rows.values() if row.id == value), None)

    monkeypatch.setattr(drafts, "get_client", get_client)
    app = create_app(resolver=Resolver(FakeRegistry(rows)))
    app.state.platform = FakePlatform()
    return TestClient(app)


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_draft_routes_are_for_owners_and_known_clients(
    api: TestClient, rows: dict[str, Client], method: str, path: str
) -> None:
    url = f"/owner/clients/{rows['pilot-local'].id}/draft{path}"
    body = {"json": {}} if method == "patch" else {}
    assert api.request(method, url, **body).status_code == 401
    assert api.request(method, url, headers=bearer(token("client")), **body).status_code == 403
    unknown = f"/owner/clients/{uuid.uuid4()}/draft{path}"
    response = api.request(method, unknown, headers=bearer(token("owner", client=None)), **body)
    assert response.status_code == 404
    assert response.json()["detail"] == "No such client"


# --- integration: the pilot vault --------------------------------------------------------------


@pytest.fixture
def pilot(clients: Clients) -> Iterator[Clients]:  # noqa: F811 (test_publish.py fixture)
    """pilot's vault with the Matrix Retail draft and an empty change log (truncate is the only
    way to empty it: the trigger refuses deletes, and zm_api can't truncate)."""
    vault_sql("pilot", "truncate app.draft_changes")
    vault_sql(
        "pilot",
        "insert into app.drafts (id, workflow, template_key) values (1, %s, 'matrix-retail')",
        json.dumps(matrix_retail()),
    )
    yield clients
    vault_sql("pilot", "truncate app.draft_changes")


def owner_api(c: Clients) -> tuple[TestClient, dict[str, str], str]:
    owner = jwt.encode(
        {"sub": str(c.owner), "kind": "owner", "exp": 4102444800}, JWT_SECRET, algorithm="HS256"
    )
    return TestClient(create_app()), bearer(owner), f"/owner/clients/{c.ids['pilot']}/draft"


@pytest.mark.integration
def test_three_changes_and_two_undos_leave_change_one_and_five_rows(pilot: Clients) -> None:
    async def change(patch: list[dict[str, Any]], summary: str) -> None:
        async with Stack(pilot) as stack:
            await drafts.apply_change(
                await stack.ctx("pilot"), pilot.owner, "test", patch, summary, uuid.uuid4()
            )

    after_one, _ = drafts.patched(matrix_retail(), CHANGES[0])
    for i, patch in enumerate(CHANGES):
        asyncio.run(change(patch, f"change {i + 1}"))
    api, auth, url = owner_api(pilot)
    with api:
        for _ in range(2):
            assert api.post(f"{url}/undo", headers=auth).status_code == 200
        draft = api.get(url, headers=auth).json()
        log = api.get(f"{url}/changes", headers=auth).json()
    assert canonical(draft["workflow"]) == canonical(after_one)
    assert draft["updated_by"] == str(pilot.owner)
    assert [(c["summary"], c["author_owner_id"]) for c in log] == [
        ("Undo: change 2", str(pilot.owner)),
        ("Undo: change 3", str(pilot.owner)),
        ("change 3", str(pilot.owner)),
        ("change 2", str(pilot.owner)),
        ("change 1", str(pilot.owner)),
    ]
    assert [c["undoes"] for c in log[:2]] == [log[3]["id"], log[2]["id"]]
    assert log[2]["conversation_id"] is not None and log[0]["conversation_id"] is None
    assert "inverse" not in log[0]  # undo's business; a whole-document inverse would be large


@pytest.mark.integration
def test_an_invalid_change_writes_nothing(pilot: Clients) -> None:
    api, auth, url = owner_api(pilot)
    with api:
        response = api.patch(f"{url}/theme", headers=auth, json={"theme": ""})
        draft = api.get(url, headers=auth).json()
        log = api.get(f"{url}/changes", headers=auth).json()
        undo = api.post(f"{url}/undo", headers=auth)
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["errors"]] == ["apps.theme"]
    assert canonical(draft["workflow"]) == canonical(matrix_retail())
    assert log == []
    assert undo.status_code == 409


@pytest.mark.integration
def test_the_theme_picker_logs_a_change_and_skips_a_no_op(pilot: Clients) -> None:
    api, auth, url = owner_api(pilot)
    with api:
        set_ = api.patch(f"{url}/theme", headers=auth, json={"theme": "warm", "accent": None})
        same = api.patch(f"{url}/theme", headers=auth, json={"theme": "warm"})
        log = api.get(f"{url}/changes", headers=auth).json()
        errors = api.get(f"{url}/validate", headers=auth).json()
    assert set_.status_code == same.status_code == 200
    assert set_.json()["workflow"]["apps"]["theme"] == "warm"
    assert set_.json()["workflow"]["apps"]["accent"] is None
    assert [(c["tool"], c["summary"]) for c in log] == [
        ("set_theme", "Theme: warm; Accent: preset default")
    ]
    assert errors == []


@pytest.mark.integration
def test_the_diff_is_against_the_active_release(pilot: Clients) -> None:
    vault_sql(
        "pilot",
        "insert into app.releases (version, deployment_id, workflow, forms, ui, routing_vars,"
        " process_keys, sha256, active) values (1, %s, %s, '{}', '{}', '{}', '{}', 'test', true)",
        f"test-{uuid.uuid4().hex[:8]}",
        json.dumps(live(matrix_retail())),
    )
    api, auth, url = owner_api(pilot)
    with api:
        empty = api.get(f"{url}/diff", headers=auth).json()

        async def add_field() -> None:
            async with Stack(pilot) as stack:
                ctx = await stack.ctx("pilot")
                await drafts.apply_change(ctx, pilot.owner, "add_field", CHANGES[1], "Added GST no")

        asyncio.run(add_field())
        one = api.get(f"{url}/diff", headers=auth).json()
    assert empty == {
        "stages": {"added": [], "removed": [], "changed": []},
        "tasks": {"added": [], "removed": [], "changed": []},
        "fields": {"added": [], "removed": [], "changed": []},
        "blocks": [],
        "theme": None,
        "accent": None,
    }
    assert one["fields"] == {"added": ["gst_no"], "removed": [], "changed": []}
    assert {**one, "fields": empty["fields"]} == empty


@pytest.mark.integration
def test_the_change_log_refuses_updates_and_deletes(pilot: Clients) -> None:
    api, auth, url = owner_api(pilot)
    with api:
        assert api.patch(f"{url}/theme", headers=auth, json={"theme": "warm"}).status_code == 200
    for statement in (
        "update app.draft_changes set summary = 'rewritten'",
        "delete from app.draft_changes",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="insert-only"):
            vault_sql("pilot", statement)  # the superuser: the trigger
        with (
            psycopg.connect(PILOT_DSN) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute(statement)  # zm_api, the API's role: no grant
    assert vault_sql("pilot", "select summary from app.draft_changes") == [("Theme: warm",)]


def change(c: Clients, *patches: list[dict[str, Any]]) -> None:
    """Apply `patches` at the same time (each its own connection), logged as 'change <n>'."""

    async def go() -> None:
        async with Stack(c) as stack:
            ctx = await stack.ctx("pilot")
            await asyncio.gather(
                *(
                    drafts.apply_change(ctx, c.owner, "test", p, f"change {CHANGES.index(p) + 1}")
                    for p in patches
                )
            )

    asyncio.run(go())


@pytest.mark.integration
def test_undo_walks_back_through_changes_made_after_an_undo(pilot: Clients) -> None:
    api, auth, url = owner_api(pilot)
    with api:
        change(pilot, CHANGES[0])
        change(pilot, CHANGES[1])
        assert api.post(f"{url}/undo", headers=auth).status_code == 200  # change 2
        change(pilot, CHANGES[2])
        undone = [api.post(f"{url}/undo", headers=auth).json() for _ in range(2)]  # 3, then 1
        nothing = api.post(f"{url}/undo", headers=auth)
        log = api.get(f"{url}/changes", headers=auth).json()
    assert canonical(undone[-1]["workflow"]) == canonical(matrix_retail())
    assert nothing.status_code == 409
    assert [c["summary"] for c in log] == [
        "Undo: change 1",
        "Undo: change 3",
        "change 3",
        "Undo: change 2",
        "change 2",
        "change 1",
    ]


@pytest.mark.integration
def test_two_changes_at_once_both_land_one_after_the_other(pilot: Clients) -> None:
    change(pilot, CHANGES[0], CHANGES[1])
    api, auth, url = owner_api(pilot)
    with api:
        draft = api.get(url, headers=auth).json()["workflow"]
        for _ in range(2):
            assert api.post(f"{url}/undo", headers=auth).status_code == 200
        back = api.get(url, headers=auth).json()["workflow"]
    assert draft["fields"]["site_name"]["label"] == "Site name (as on the lease)"
    assert "gst_no" in draft["fields"]
    assert canonical(back) == canonical(
        matrix_retail()
    )  # each inverse fit the draft it was made on


@pytest.mark.integration
def test_undo_refuses_to_cross_a_draft_write_that_skipped_the_log(pilot: Clients) -> None:
    async def reseed() -> None:
        async with Stack(pilot) as stack:
            await releases.seed_draft(await stack.ctx("pilot"), "matrix-retail")

    api, auth, url = owner_api(pilot)
    with api:
        change(pilot, CHANGES[0])
        asyncio.run(reseed())  # `zm-api seed-draft`: writes no change row
        refused = api.post(f"{url}/undo", headers=auth)
        change(pilot, CHANGES[1])  # a logged change after the reseed can still be undone
        later = api.post(f"{url}/undo", headers=auth)
        behind = api.post(f"{url}/undo", headers=auth)  # change 1 is still behind the reseed
    assert refused.status_code == 409 and "outside the change log" in refused.json()["detail"]
    assert later.status_code == 200
    assert canonical(later.json()["workflow"]) == canonical(matrix_retail())
    assert behind.status_code == 409


@pytest.mark.integration
def test_undo_refuses_after_a_direct_sql_edit(pilot: Clients) -> None:
    change(pilot, CHANGES[0])
    vault_sql(
        "pilot", "update app.drafts set workflow = jsonb_set(workflow, '{apps,accent}', '\"plum\"')"
    )
    api, auth, url = owner_api(pilot)
    with api:
        refused = api.post(f"{url}/undo", headers=auth)
        draft = api.get(url, headers=auth).json()["workflow"]
    assert refused.status_code == 409
    assert draft["apps"]["accent"] == "plum"  # left as the edit made it


@pytest.mark.integration
def test_undo_reaches_a_state_todays_rules_reject(
    pilot: Clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    change(pilot, CHANGES[0])
    monkeypatch.setattr(drafts, "validate", lambda doc: [drafts.Error("$", "stricter rules now")])
    api, auth, url = owner_api(pilot)
    with api:
        undone = api.post(f"{url}/undo", headers=auth)
    assert undone.status_code == 200
    assert canonical(undone.json()["workflow"]) == canonical(matrix_retail())


@pytest.mark.integration
def test_the_theme_picker_works_on_a_draft_without_apps(pilot: Clients) -> None:
    vault_sql("pilot", "update app.drafts set workflow = workflow - 'apps'")  # `apps` is optional
    api, auth, url = owner_api(pilot)
    with api:
        response = api.patch(f"{url}/theme", headers=auth, json={"theme": "warm"})
    assert response.status_code == 200
    assert response.json()["workflow"]["apps"] == {"theme": "warm"}
