import json

import pytest

from zm_compiler import compile

from .conftest import TEMPLATES, template


@pytest.mark.parametrize("key", ["matrix-retail", "qsr"])
def test_compiling_twice_is_byte_identical(key: str) -> None:
    first, second = compile(template(key), key), compile(template(key), key)
    assert first.bpmn == second.bpmn
    assert first.canonical() == second.canonical()
    assert first.digest() == second.digest()


@pytest.mark.parametrize("key", ["matrix-retail", "qsr"])
def test_json_round_trip_gives_the_same_release(key: str) -> None:
    from zm_schema import migrate

    raw = json.loads((TEMPLATES / key / "workflow.json").read_text())
    again = migrate(json.loads(json.dumps(raw, sort_keys=True)))
    assert compile(again, key).digest() == compile(template(key), key).digest()
