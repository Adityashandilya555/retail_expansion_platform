import copy
import re
import xml.etree.ElementTree as ET
from typing import Any

import pytest

from zm_compiler import CompileError, compile, process_key

from .conftest import doc, template

NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
OP = "{http://operaton.org/schema/1.0/bpmn}"


def tasks(xml: bytes) -> dict[str, ET.Element]:
    root = ET.fromstring(xml)
    return {t.get("id"): t for t in root.iter(f"{{{NS['bpmn']}}}userTask")}


def test_process_keys_and_resources(two_stage: dict[str, Any]) -> None:
    r = compile(doc(two_stage), "matrix-retail")
    assert r.process_keys == {"store_launch": "zm_matrix_retail__store_launch"}
    assert list(r.bpmn) == [
        "zm_matrix_retail__store_launch.bpmn",
        "zm_matrix_retail__store_launch__bd.bpmn",
        "zm_matrix_retail__store_launch__legal.bpmn",
    ]
    assert process_key("qsr", "x") == "zm_qsr__x"


def test_two_flows_in_one_release(two_stage: dict[str, Any]) -> None:
    second = copy.deepcopy(two_stage["flows"][0])
    second["key"] = "closure"
    for stage in second["stages"]:
        for t in stage["tasks"]:
            t["id"] = "cl_" + t["id"]
    two_stage["flows"].append(second)
    r = compile(doc(two_stage), "qsr")
    assert r.process_keys == {"store_launch": "zm_qsr__store_launch", "closure": "zm_qsr__closure"}
    mains = [k for k in r.bpmn if k.count("__") == 1]
    assert mains == ["zm_qsr__store_launch.bpmn", "zm_qsr__closure.bpmn"]
    assert len(r.bpmn) == 6
    assert {"start:store_launch", "start:closure"} <= set(r.forms)


def test_or_above_lists_every_higher_level(two_stage: dict[str, Any]) -> None:
    r = compile(doc(two_stage), "t")
    legal = tasks(r.bpmn["zm_t__store_launch__legal.bpmn"])["legal_review"]
    assert legal.get(OP + "candidateGroups") == "legal__L1,legal__L2"
    qsr = compile(template("qsr"), "qsr")
    lic = tasks(qsr.bpmn["zm_qsr__store_launch__statutory.bpmn"])["qsr_licences"]
    assert lic.get(OP + "candidateGroups") == "legal__L1,legal__L2,legal__L3"


def test_assignees_for_initiator_and_user_field() -> None:
    r = compile(template("matrix-retail"), "matrix-retail")
    bd = tasks(r.bpmn["zm_matrix_retail__store_launch__bd_qualification.bpmn"])
    assert bd["bd_site_details"].get(OP + "assignee") == "${initiator}"
    design = tasks(r.bpmn["zm_matrix_retail__store_launch__design.bpmn"])
    assert design["design_recce"].get(OP + "assignee") == "${design_exec}"
    assert "design_exec" in r.routing_vars["design_allocate"]


def test_every_form_key_is_a_plain_zm_key() -> None:
    for key in ("matrix-retail", "qsr"):
        r = compile(template(key), key)
        for xml in r.bpmn.values():
            text = xml.decode()
            keys = re.findall(r'operaton:formKey="([^"]*)"', text)
            assert keys and all(k.startswith("zm:") and "${" not in k for k in keys)
            assert "historyTimeToLive" not in text
        for t_id in r.forms:
            if not t_id.startswith("start:"):
                assert f'operaton:formKey="zm:{t_id}"' in "".join(
                    x.decode() for x in r.bpmn.values()
                )


def test_initiator_at_least_and_skip_gateway() -> None:
    r = compile(template("matrix-retail"), "matrix-retail")
    xml = r.bpmn["zm_matrix_retail__store_launch__bd_qualification.bpmn"].decode()
    assert "${!((initiator_groups.contains(&#x27;,bd__L2,&#x27;)))}" in xml or (
        "initiator_groups.contains(',bd__L2,')" in xml
    )
    assert 'id="bd_review_draft_skip"' in xml
    assert "${(initiator_groups.contains(',bd__L2,'))}" in xml


def test_route_has_an_otherwise_flow() -> None:
    r = compile(template("matrix-retail"), "matrix-retail")
    xml = r.bpmn["zm_matrix_retail__store_launch__legal.bpmn"].decode()
    assert 'name="Some pending"' in xml and 'name="Otherwise"' in xml
    assert "!(!(lic_fssai &amp;&amp; lic_health_trade" in xml
    assert {"lic_fssai", "lic_fire_noc"} <= set(r.routing_vars["legal_licensing"])


def test_parallel_group_splits_and_joins() -> None:
    r = compile(template("matrix-retail"), "matrix-retail")
    xml = r.bpmn["zm_matrix_retail__store_launch__nso.bpmn"].decode()
    assert xml.count("<bpmn:parallelGateway") == 2


def test_main_process_joins_parallel_stages() -> None:
    r = compile(template("matrix-retail"), "matrix-retail")
    xml = r.bpmn["zm_matrix_retail__store_launch.bpmn"].decode()
    assert 'id="split_bd_loi"' in xml and 'id="join_design"' in xml
    assert 'calledElement="zm_matrix_retail__store_launch__legal"' in xml
    assert 'operaton:calledElementBinding="deployment"' in xml
    assert 'operaton:candidateStarterGroups="bd__L1,bd__L2"' in xml


def test_groups_follow_the_org() -> None:
    r = compile(template("qsr"), "qsr")
    assert [g.id for g in r.groups][:5] == [
        "bd__L1",
        "bd__L2",
        "legal__L1",
        "legal__L2",
        "legal__L3",
    ]
    assert r.groups[2].name == "Legal & Statutory L1 · Legal Executive"


def test_ui_is_the_apps_section_unchanged() -> None:
    wf = template("matrix-retail")
    r = compile(wf, "matrix-retail")
    assert r.ui == wf.apps.model_dump(mode="json", by_alias=True)
    assert all("apps" not in x.decode() and "task_inbox" not in x.decode() for x in r.bpmn.values())


def test_bad_slug_is_refused(two_stage: dict[str, Any]) -> None:
    with pytest.raises(CompileError, match="client slug"):
        compile(doc(two_stage), "Bad Slug")


def test_invalid_document_is_refused(two_stage: dict[str, Any]) -> None:
    wf = doc(two_stage)
    broken = wf.model_copy(deep=True)
    broken.flows[0].stages[1].tasks[0].outcomes[0].to = "nowhere"  # type: ignore[union-attr]
    with pytest.raises(CompileError, match="invalid workflow"):
        compile(broken, "t")
