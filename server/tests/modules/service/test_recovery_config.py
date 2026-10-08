"""Service configuration routes (03 spec 1.7): departments, request types,
amenities (read through amenities.api), SLA policies and the compensation
authority matrix (Decimal strings in, minor units stored), plus complaint
categories. Defaults show through until a hotel overrides; reset drops the
override."""

from __future__ import annotations

import pytest

from clefs.modules.service import config, config_api

pytestmark = pytest.mark.usefixtures("_ops_routes")


@pytest.fixture
def _ops_routes(app):
    config_api.mount_ops(app)


def test_departments_and_request_types_override_and_reset(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    gm = client_as("gm")
    base = gm.get(f"/hotels/{s}/service/config/departments").json()
    hk = next(d for d in base if d["key"] == "housekeeping")
    assert hk["source"] == "default"
    r = gm.put(f"/hotels/{s}/service/config/departments", json={"departments": [
        {"key": "spa", "name": "Spa", "name_ar": "سبا", "color": "#aa22cc"},
        {"key": "housekeeping", "name": "Rooms Division"}]})
    assert r.status_code == 200, r.text
    rows = {d["key"]: d for d in r.json()}
    assert rows["spa"]["source"] == "hotel" and rows["spa"]["legacy_category"] == "Spa"
    assert rows["housekeeping"]["name"] == "Rooms Division" and rows["housekeeping"]["legacy_category"] == "Housekeeping"
    t = gm.put(f"/hotels/{s}/service/config/request-types", json={"request_types": [
        {"key": "massage", "label_en": "Massage booking", "department_key": "spa", "default_severity": "L3"}]})
    assert t.status_code == 200 and config.request_type(cur, s, "massage")["department_key"] == "spa"
    bad = gm.put(f"/hotels/{s}/service/config/request-types", json={"request_types": [
        {"key": "x", "label_en": "X", "department_key": "nowhere"}]})
    assert bad.status_code == 400 and bad.json()["code"] == "unknown_department"
    back = gm.put(f"/hotels/{s}/service/config/departments", json={"reset": ["housekeeping"]}).json()
    assert next(d for d in back if d["key"] == "housekeeping")["source"] == "default"
    assert client_as("staff").put(f"/hotels/{s}/service/config/departments", json={}).status_code == 403


def test_sla_policies_merge_and_validate(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    gm = client_as("gm")
    rows = gm.get(f"/hotels/{s}/service/config/sla-policies").json()
    towels = next(r for r in rows if r["scope_type"] == "request_type" and r["scope_key"] == "towels")
    assert (towels["ack_min"], towels["resolve_min"], towels["source"]) == (3, 15, "default")
    r = gm.put(f"/hotels/{s}/service/config/sla-policies", json={"policies": [
        {"scope_type": "request_type", "scope_key": "towels", "ack_min": 2, "resolve_min": 12,
         "ladder": [{"after_min": 0, "to": "assignee"}, {"after_min": 2, "to": "supervisor"}]}]})
    assert r.status_code == 200, r.text
    got = next(x for x in r.json() if x["scope_key"] == "towels")
    assert got["source"] == "hotel" and got["ack_min"] == 2
    p = config.sla_policy(cur, s, request_type_key="towels", department_key="housekeeping", severity="L3")
    assert p["ref"] == "hotel:request_type:towels" and p["ack_min"] == 2
    bad = gm.put(f"/hotels/{s}/service/config/sla-policies", json={"policies": [
        {"scope_type": "department_severity", "scope_key": "housekeeping:L9", "ack_min": 1, "resolve_min": 5}]})
    assert bad.status_code == 400 and bad.json()["code"] == "unknown_scope"
    order = gm.put(f"/hotels/{s}/service/config/sla-policies", json={"policies": [
        {"scope_type": "default", "ack_min": 5, "resolve_min": 30,
         "ladder": [{"after_min": 5, "to": "supervisor"}, {"after_min": 3, "to": "gm"}]}]})
    assert order.status_code == 400
    reset = gm.put(f"/hotels/{s}/service/config/sla-policies",
                   json={"reset": [{"scope_type": "request_type", "scope_key": "towels"}]}).json()
    assert next(x for x in reset if x["scope_key"] == "towels")["source"] == "default"


def test_compensation_matrix_in_minor_units(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    gm = client_as("gm")
    m = gm.get(f"/hotels/{s}/service/config/compensation").json()
    staff = next(r for r in m["roles"] if r["role_key"] == "staff")
    assert (m["currency"], staff["max_minor"], staff["max_value"]) == ("AED", 10000, "100.00")
    assert next(r for r in m["roles"] if r["role_key"] == "gm")["max_minor"] is None
    r = gm.put(f"/hotels/{s}/service/config/compensation", json={"roles": [
        {"role_key": "staff", "max_value": "150.50", "allowed_kinds": ["amenity", "fnb_credit", "late_checkout"]}]})
    assert r.status_code == 200, r.text
    cur.execute("SELECT max_minor, currency FROM svc_compensation_authority WHERE hotel_slug = %s AND role_key = 'staff'", (s,))
    row = cur.fetchone()
    assert row["max_minor"] == 15050 and row["currency"].strip() == "AED"
    head = client_as("dept_head")
    wider = head.put(f"/hotels/{s}/service/config/compensation", json={"roles": [
        {"role_key": "staff", "max_value": "500", "allowed_kinds": ["amenity"]}]})
    assert wider.status_code == 403
    narrower = head.put(f"/hotels/{s}/service/config/compensation", json={"roles": [
        {"role_key": "staff", "max_value": "50", "allowed_kinds": ["amenity"]}]})
    assert narrower.status_code == 200
    assert next(x for x in narrower.json()["roles"] if x["role_key"] == "staff")["max_value"] == "50.00"


def test_amenities_read_through_and_categories(client_as, hotel_uae):
    s = hotel_uae.slug
    gm = client_as("gm")
    assert gm.get(f"/hotels/{s}/service/config/amenities").json() == []  # the Amenities stub has no items
    assert gm.put(f"/hotels/{s}/service/config/amenities").json()["code"] == "managed_by_amenities"
    r = gm.put(f"/hotels/{s}/service/config/complaint-categories", json={"categories": [
        {"key": "pool", "label_en": "Pool", "department_key": "engineering"}]})
    keys = {c["key"]: c for c in r.json()}
    assert keys["pool"]["source"] == "hotel" and keys["noise"]["source"] == "default"
