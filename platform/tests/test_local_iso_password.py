"""scripts/local_iso_password.py (#31): rotate operaton-iso's service password once, never twice."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/local_iso_password.py"


def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("local_iso_password", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def engine(new_works: bool, rotate_status: int, seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(200 if new_works else 401, json={"count": 1})
        return httpx.Response(rotate_status)

    return httpx.Client(
        base_url="http://iso.test/engine-rest", transport=httpx.MockTransport(handler)
    )


def test_an_engine_with_its_own_password_is_left_alone(capsys: pytest.CaptureFixture) -> None:
    seen: list[str] = []
    assert script().main(engine(True, 204, seen), sleep=lambda s: None) == 0
    assert seen == ["GET /engine-rest/user/count"]
    assert "already its own" in capsys.readouterr().out


def test_an_old_engine_is_rotated_after_the_login_delay() -> None:
    seen: list[str] = []
    slept: list[float] = []
    assert script().main(engine(False, 204, seen), sleep=slept.append) == 0
    assert seen == ["GET /engine-rest/user/count", "PUT /engine-rest/user/zm-service/credentials"]
    assert slept == [4]


def test_a_refused_rotation_says_so(capsys: pytest.CaptureFixture) -> None:
    assert script().main(engine(False, 401, []), sleep=lambda s: None) == 1
    assert "couldn't rotate" in capsys.readouterr().out


def test_dotenv_reads_values_and_skips_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nZM_ISO_ADMIN_USER_PASSWORD=custom  # trailing\nBROKEN\n\nA=1\n")
    assert script().dotenv(env) == {"ZM_ISO_ADMIN_USER_PASSWORD": "custom", "A": "1"}
    assert script().dotenv(tmp_path / "missing") == {}
