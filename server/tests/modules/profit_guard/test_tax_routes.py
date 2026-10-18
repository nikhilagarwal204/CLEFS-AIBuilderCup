"""Profit Guard's thin tax routes over clefs.core.tax (02 spec 3.8): the
settings Tax page reads the fee rules and the profile, and owners switch a
rule on or off. Finance may manage; front desk may not read."""

from __future__ import annotations


def test_fee_rules_profile_and_override(client_as, hotel_uae):
    s = hotel_uae.slug
    owner = client_as("owner")
    r = owner.get(f"/hotels/{s}/profit/tax/fee-rules?on=2026-10-01")
    assert r.status_code == 200, r.text
    rules = r.json()["rules"]
    assert rules and {"id", "rule_key", "fee_code", "rate", "override"} <= set(rules[0])
    p = owner.get(f"/hotels/{s}/profit/tax/profile")
    assert p.status_code == 200 and p.json()["currency"] == "AED"
    put = owner.put(f"/hotels/{s}/profit/tax/profile",
                    json={"effective_from": "2026-10-01", "property_category": "hotel_4", "convention": "A"})
    assert put.status_code == 200, put.text
    assert owner.get(f"/hotels/{s}/profit/tax/profile?on=2026-10-02").json()["property_category"] == "hotel_4"
    rule = rules[0]
    o = owner.post(f"/hotels/{s}/profit/tax/overrides",
                   json={"fee_rule_id": rule["id"], "rule_key": rule["rule_key"], "apply": False, "note": "test"})
    assert o.status_code == 200, o.text
    again = {x["id"]: x for x in owner.get(f"/hotels/{s}/profit/tax/fee-rules").json()["rules"]}
    assert again[rule["id"]]["override"]["apply"] is False
    assert owner.post(f"/hotels/{s}/profit/tax/overrides", json={"apply": True}).json()["code"] == "rule_required"


def test_tax_routes_follow_profit_permissions(client_as, hotel_uae):
    s = hotel_uae.slug
    assert client_as("front_desk").get(f"/hotels/{s}/profit/tax/fee-rules").status_code == 403
    fin = client_as("finance")
    assert fin.get(f"/hotels/{s}/profit/tax/profile").status_code == 200
