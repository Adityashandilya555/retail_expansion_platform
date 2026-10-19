from typing import Any

import pytest
from jsonschema import Draft202012Validator

from zm_compiler import compile
from zm_compiler.forms import field_schema
from zm_schema.models import FieldDef

from .conftest import template

DOC = {"key": "sites/MX-0001/t/s/x.pdf", "sha256": "a" * 64, "name": "x.pdf"}


def validator(form_id: str) -> Draft202012Validator:
    r = compile(template("qsr"), "qsr")
    schema = r.forms[form_id].schema
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def errors(form_id: str, payload: dict[str, Any]) -> list[str]:
    return [e.message for e in validator(form_id).iter_errors(payload)]


def test_valid_payload_passes() -> None:
    assert (
        errors(
            "qsr_licences",
            {"fssai_no": "123", "licence_valid_until": "2027-01-31", "fire_noc": DOC},
        )
        == []
    )


def test_missing_required_field_fails() -> None:
    assert any("'fssai_no' is a required property" in m for m in errors("qsr_licences", {}))


def test_unknown_field_fails() -> None:
    msgs = errors("qsr_licences", {"fssai_no": "1", "surprise": 1})
    assert any("Additional properties are not allowed" in m for m in msgs)


def test_outcome_enum_and_comment_rule() -> None:
    assert errors("qsr_site_signoff", {"qsr_site_signoff_outcome": "approve"}) == []
    assert errors("qsr_site_signoff", {"qsr_site_signoff_outcome": "maybe"})
    assert any(
        "'qsr_site_signoff_comment' is a required property" in m
        for m in errors("qsr_site_signoff", {"qsr_site_signoff_outcome": "send_back"})
    )
    assert (
        errors(
            "qsr_site_signoff",
            {"qsr_site_signoff_outcome": "reject", "qsr_site_signoff_comment": "no"},
        )
        == []
    )


def test_approval_form() -> None:
    v = validator("qsr_licences__appr_legal_L2")
    assert not list(v.iter_errors({"qsr_licences__appr_legal_L2_outcome": "approve"}))
    assert list(v.iter_errors({"qsr_licences__appr_legal_L2_outcome": "send_back"}))


def test_layout_and_metadata() -> None:
    r = compile(template("qsr"), "qsr")
    capex = r.forms["qsr_capex"].schema
    assert capex["x-zm-layout"][0]["hidden_below_level"] == {"department": "budget", "level": 1}
    assert capex["properties"]["capex_total"]["x-zm-type"] == "currency"
    assert capex["x-zm-show"] == ["fssai_no", "monthly_rent"]
    start = r.forms["start:store_launch"].schema
    assert start["required"] == ["store_name", "city"]


@pytest.mark.parametrize(
    ("ftype", "value", "ok"),
    [
        ("rating", 3, True),
        ("rating", 9, False),
        ("date_range", {"from": "2026-01-01", "to": "2026-02-01"}, True),
        ("multi_select", ["a", "a"], False),
        ("multi_select", ["a", "b"], True),
        ("checklist", {"a": "na", "b": "yes"}, True),
        ("checklist", {"a": "maybe"}, False),
        ("line_items", [{"label": "HVAC", "amount": 10}], True),
        ("address", {"text": "MG Road", "lat": 1.0}, True),
        ("yes_no", "yes", False),
        ("percent", 12.5, True),
        ("computed", 1.0, True),
    ],
)
def test_field_types(ftype: str, value: Any, ok: bool) -> None:
    options = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    f = FieldDef.model_validate(
        {
            "type": ftype,
            "label": "X",
            **({"options": options} if "select" in ftype or ftype == "checklist" else {}),
        }
    )
    schema = field_schema("x", f)
    assert schema is not None and schema["x-zm-type"] == ftype
    assert (not list(Draft202012Validator(schema).iter_errors(value))) is ok


def test_info_fields_have_no_value() -> None:
    assert field_schema("x", FieldDef(type="info", label="Note")) is None
