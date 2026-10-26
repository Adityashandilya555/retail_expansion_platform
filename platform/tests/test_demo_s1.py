"""The Sprint 1 demo driver's pure parts (#31), in the unit run; the drive itself runs against
the local stack in tests/isolation/test_demo_driver.py."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

DEMO = Path(__file__).resolve().parents[1] / "scripts/demo_s1.py"


def demo_driver() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_s1", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_check_cases_api_warns_while_the_route_is_missing(capsys: pytest.CaptureFixture) -> None:
    import httpx

    demo = demo_driver()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"paths": {"/app/cases": {"post": {}}}})
        return httpx.Response(404, json={"detail": "Not Found"})

    http = httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))
    session = demo.Session(http, {"bd2": "t"})
    assert demo.check_cases_api(http, session, ["MX-0001"]) is False
    assert "isn't there yet (#91)" in capsys.readouterr().out


def test_check_cases_api_needs_every_code_once_it_exists() -> None:
    import httpx

    demo = demo_driver()
    body = [{"code": "MX-0001"}, {"code": "MX-0002"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"paths": {"/app/cases": {"get": {}, "post": {}}}})
        return httpx.Response(200, json=body)

    http = httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))
    session = demo.Session(http, {"bd2": "t"})
    assert demo.check_cases_api(http, session, ["MX-0001", "MX-0002"]) is True
    with pytest.raises(demo.DemoError, match="MX-0003"):
        demo.check_cases_api(http, session, ["MX-0001", "MX-0003"])
