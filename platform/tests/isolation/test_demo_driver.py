"""The Sprint 1 demo driver (#31) against pilot-local: three cases at three stages, through the
API only, the same run the run-book does against the cloud pilot."""

import pytest

from .conftest import PILOT, World, demo_driver

pytestmark = pytest.mark.isolation


def test_the_demo_driver_puts_three_cases_at_three_stages(world: World) -> None:
    demo = demo_driver()
    users = [
        {
            "email": world.pilot.email(role),
            "password": world.pilot.passwords[role],
            "department": department,
            "level": level,
        }
        for role, (department, level) in PILOT.users.items()
    ]
    first, second, third = demo.drive(world.http, PILOT.code, users)
    assert len({first, second, third}) == 3

    session = demo.sign_in(world.http, PILOT.code, users)
    assert "legal" in demo.open_stages(session, first)
    assert demo.open_stages(session, second) == {"bd_qualification"}
    assert "legal" not in demo.open_stages(session, third)
    legal_l1 = session.ok("legal1", "GET", "/app/tasks")
    assert any(
        t["case_code"] == first and t["form_key"] == "zm:legal_dd_checklist" for t in legal_l1
    )


def test_fill_answers_required_fields_and_every_yes_no() -> None:
    demo = demo_driver()
    schema = {
        "required": ["name", "amount", "on", "doc", "kind"],
        "properties": {
            "name": {"type": "string"},
            "amount": {"type": "number"},
            "on": {"type": "string", "format": "date"},
            "doc": {"type": "object", "x-zm-type": "document"},
            "kind": {"type": "string", "enum": ["a", "b"]},
            "ok": {"type": "boolean", "x-zm-type": "yes_no"},
            "optional": {"type": "string"},
            "t_outcome": {"type": "string", "enum": ["x"], "x-zm-type": "outcome"},
        },
    }
    values = demo.fill(schema)
    assert set(values) == {"name", "amount", "on", "doc", "kind", "ok"}
    assert values["kind"] == "a" and values["ok"] is True and values["amount"] == 1
    assert values["doc"]["sha256"] == "0" * 64


def test_pick_users_names_the_missing_role() -> None:
    demo = demo_driver()
    with pytest.raises(demo.DemoError, match="legal L2"):
        demo.pick_users(
            [
                {"email": "a", "password": "p", "department": "bd", "level": 1},
                {"email": "b", "password": "p", "department": "bd", "level": 2},
                {"email": "c", "password": "p", "department": "legal", "level": 1},
            ]
        )
