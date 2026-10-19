"""Golden files: set ZM_UPDATE_GOLDEN=1 to rewrite them after an intended change."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from zm_compiler import compile

from .conftest import GOLDEN, doc, template

UPDATE = os.environ.get("ZM_UPDATE_GOLDEN") == "1"


def check(path: Path, content: bytes) -> None:
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert path.exists(), f"missing golden file {path.name}: run with ZM_UPDATE_GOLDEN=1"
    assert path.read_bytes() == content, f"{path.name} differs: review, then ZM_UPDATE_GOLDEN=1"


def test_approval_chain() -> None:
    """QSR's Legal stage: a task by Legal L1+, then Legal L2 -> L3 approvals with send-back."""
    r = compile(template("qsr"), "qsr")
    xml = r.bpmn["zm_qsr__store_launch__statutory.bpmn"]
    check(GOLDEN / "approval_chain.bpmn", xml)
    text = xml.decode()
    assert text.index('id="qsr_licences__appr_legal_L2"') < text.index(
        'id="qsr_licences__appr_legal_L3"'
    )
    assert 'targetRef="qsr_licences" name="Send back"' in text


def test_case_status(two_stage: dict[str, Any]) -> None:
    r = compile(doc(two_stage), "t")
    xml = r.bpmn["zm_t__store_launch.bpmn"]
    check(GOLDEN / "case_status_main.bpmn", xml)
    text = xml.decode()
    assert "execution.setVariable(&#x27;case_status&#x27;, &#x27;open&#x27;)" in text or (
        "setVariable('case_status', 'open')" in text
    )
    assert "setVariable('case_status', 'completed')" in text
    assert "setVariable('case_status', 'closed:legal')" in text
    assert "setVariable('case_status', 'closed:bd')" in text


@pytest.mark.parametrize(
    "form_id", ["qsr_licences", "qsr_site_signoff", "qsr_licences__appr_legal_L3"]
)
def test_forms(form_id: str) -> None:
    r = compile(template("qsr"), "qsr")
    blob = json.dumps(r.forms[form_id].schema, indent=2, sort_keys=True) + "\n"
    check(GOLDEN / "forms" / f"{form_id}.json", blob.encode())
