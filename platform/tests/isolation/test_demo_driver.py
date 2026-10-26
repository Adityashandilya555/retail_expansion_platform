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
