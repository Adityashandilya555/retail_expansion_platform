"""The Sprint 1 demo driver's pure parts (#31), in the unit run; the drive itself runs against
the local stack in tests/isolation/test_demo_driver.py."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
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


def _row(code: str, status: str = "open", **stages: str) -> dict:
    keys = ["bd_qualification", "bd_loi", "legal", "finance", "design"]
    return {
        "code": code,
        "status": status,
        "stages": [{"key": k, "state": stages.get(k, "queued")} for k in keys],
    }


GOOD = [
    _row("MX-0001", bd_qualification="done", bd_loi="done", legal="open", finance="open"),
    _row("MX-0002", bd_qualification="open"),
    _row("MX-0003", bd_qualification="done", bd_loi="done", legal="done", finance="open"),
]


def cases_api(body: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"paths": {"/app/cases": {"get": {}, "post": {}}}})
        return httpx.Response(200, json=body)

    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


def test_check_cases_api_checks_codes_and_stages_once_it_exists() -> None:
    demo = demo_driver()
    codes = ["MX-0001", "MX-0002", "MX-0003"]
    http = cases_api(GOOD)
    assert demo.check_cases_api(http, demo.Session(http, {"bd2": "t"}), codes) is True

    missing = cases_api(GOOD[:2])
    with pytest.raises(demo.DemoError, match="MX-0003"):
        demo.check_cases_api(missing, demo.Session(missing, {"bd2": "t"}), codes)

    wrong = [GOOD[0], _row("MX-0002", bd_qualification="done", bd_loi="open"), GOOD[2]]
    bad = cases_api(wrong)
    with pytest.raises(demo.DemoError, match="MX-0002: should be in bd_qualification only"):
        demo.check_cases_api(bad, demo.Session(bad, {"bd2": "t"}), codes)

    closed = cases_api([GOOD[0], GOOD[1], _row("MX-0003", "closed", legal="closed")])
    with pytest.raises(demo.DemoError, match="legal is closed, not done"):
        demo.check_cases_api(closed, demo.Session(closed, {"bd2": "t"}), codes)


def test_main_runs_the_drive_with_the_api_and_users_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
) -> None:
    import json

    import httpx

    demo = demo_driver()
    (tmp_path / ".demo-users.json").write_text(json.dumps({"users": []}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_BASE_URL", "https://api.test")
    monkeypatch.delenv("DEMO_USERS_FILE", raising=False)
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    # no users in the file: the drive stops at sign-in and says which role is missing
    assert demo.main(["--client", "PILOT"], transport=transport) == 1
    assert "no bd L1 user" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"API_BASE_URL": "file:///etc/passwd"}, "API_BASE_URL"),
        ({"API_BASE_URL": ""}, "API_BASE_URL"),
        ({"API_BASE_URL": "https://api.test", "DEMO_USERS_FILE": "../outside.json"}, "users file"),
        ({"API_BASE_URL": "https://api.test", "DEMO_USERS_FILE": "missing.json"}, "users file"),
    ],
)
def test_main_refuses_a_bad_url_or_a_file_outside_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture, env, message
) -> None:
    demo = demo_driver()
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "outside.json").write_text("{}")
    monkeypatch.chdir(work)
    for var in ("API_BASE_URL", "DEMO_USERS_FILE"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert demo.main(["--client", "PILOT"]) == 2
    assert message in capsys.readouterr().err
