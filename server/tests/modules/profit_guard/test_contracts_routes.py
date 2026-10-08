"""Profit Guard contract, program, channel-config, policy, rate-evidence,
channel-economics and expected-commission routes."""

from __future__ import annotations

from datetime import date, timedelta

from clefs import files
from clefs.core import stays
from tests.modules.profit_guard.test_contracts import CONTRACT_TEXT, extraction


def test_extract_then_confirm_contract(cur, client_as, hotel_uae, fake_ai):
    s = hotel_uae.slug
    owner = client_as("owner")
    fid = str(files.put_file(cur, s, CONTRACT_TEXT.encode(), filename="gdt.txt", content_type="text/plain",
                             purpose="profit.contract").id)
    fake_ai.push(extraction())
    r = owner.post(f"/hotels/{s}/profit-guard/contracts/extract", json={"file_id": fid})
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["status"] == "DRAFT" and draft["base_commission_pct"] == "15.000"
    assert draft["review_flags"] == ["citation_page_out_of_range:Annex 9"] and draft["source_file_id"] == fid
    listing = owner.get(f"/hotels/{s}/profit-guard/contracts").json()
    assert [c["id"] for c in listing["contracts"]] == [draft["id"]]
    assert {d["ota_code"] for d in listing["defaults"]} == {"BOOKING", "AGODA", "EXPEDIA"}
    body = {k: draft[k] for k in ("ota_code", "entity_name", "entity_country", "effective_from", "effective_to",
                                  "commission_base_rule", "excluded_charge_types", "collect_models", "dispute_rules",
                                  "vcc_rules", "clauses")}
    ok = owner.put(f"/hotels/{s}/profit-guard/contracts/{draft['id']}", json={**body, "base_commission_pct": "16"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "CONFIRMED" and ok.json()["base_commission_pct"] == "16.000"
    retired = owner.delete(f"/hotels/{s}/profit-guard/contracts/{draft['id']}")
    assert retired.json()["status"] == "RETIRED"
    assert owner.put(f"/hotels/{s}/profit-guard/contracts/{draft['id']}", json={**body, "base_commission_pct": "1"}) \
        .json()["code"] == "contract_retired"
    manual = owner.post(f"/hotels/{s}/profit-guard/contracts",
                        json={"ota_code": "Agoda", "effective_from": "2026-01-01", "base_commission_pct": "17.5",
                              "commission_base_rule": "RSR_TAX_INCL"})
    assert manual.status_code == 201 and manual.json()["ota_code"] == "AGODA"
    fake_ai.push(__import__("clefs.ai", fromlist=["AIDisabled"]).AIDisabled())
    off = owner.post(f"/hotels/{s}/profit-guard/contracts/extract", json={"file_id": fid})
    assert off.status_code == 409 and off.json()["code"] == "ai_unavailable"


def test_programs_channel_config_and_policies(client_as, hotel_uae):
    s = hotel_uae.slug
    fin = client_as("finance")
    p = fin.post(f"/hotels/{s}/profit-guard/programs",
                 json={"ota_code": "booking", "program": "GENIUS", "kind": "DISCOUNT", "pct": "10",
                       "booker_countries": ["ae", "gb"]})
    assert p.status_code == 201, p.text
    assert p.json()["ota_code"] == "BOOKING" and p.json()["booker_countries"] == ["AE", "GB"]
    agx = fin.post(f"/hotels/{s}/profit-guard/programs",
                   json={"ota_code": "AGODA", "program": "AGX", "kind": "PER_BOOKING_COST", "fixed": "12.50"})
    assert agx.json()["fixed_minor"] == 1250 and agx.json()["fixed"] == "12.50" and agx.json()["currency"] == "AED"
    bad = fin.post(f"/hotels/{s}/profit-guard/programs", json={"ota_code": "AGODA", "program": "AGX",
                                                               "kind": "PER_BOOKING_COST"})
    assert bad.status_code == 400 and bad.json()["code"] == "program_amount_required"
    upd = fin.put(f"/hotels/{s}/profit-guard/programs/{p.json()['id']}",
                  json={"ota_code": "BOOKING", "program": "GENIUS", "kind": "DISCOUNT", "pct": "15", "is_active": False})
    assert upd.json()["pct"] == "15.000" and upd.json()["is_active"] is False
    assert len(fin.get(f"/hotels/{s}/profit-guard/programs?active_only=true").json()) == 1
    assert fin.delete(f"/hotels/{s}/profit-guard/programs/{agx.json()['id']}").status_code == 204
    cfg = fin.put(f"/hotels/{s}/profit-guard/channel-config",
                  json={"ota_code": "BOOKING", "configured_from": "2026-09-01", "mf_configured_as_charge": False,
                        "payments_fee_pct": "1.1"})
    assert cfg.status_code == 200 and cfg.json()["payments_fee_pct"] == "1.1000"
    view = fin.get(f"/hotels/{s}/profit-guard/channel-config?on=2026-10-01").json()
    assert view["in_force"]["BOOKING"]["mf_configured_as_charge"] is False
    assert view["in_force"]["BOOKING"]["td_configured_as_charge"] is True
    assert view["in_force"]["AGODA"]["stored"] is False
    assert fin.delete(f"/hotels/{s}/profit-guard/channel-config/BOOKING/2026-09-01").status_code == 204
    pol = fin.get(f"/hotels/{s}/profit-guard/policies").json()
    assert pol["min_finding"] == "25.00" and pol["stored"] is False and pol["parity_tolerance_pct"] == "1.0"
    put = fin.put(f"/hotels/{s}/profit-guard/policies", json={"min_finding": "40", "treat_sc_as_revenue": False})
    assert put.json()["min_finding_minor"] == 4000 and put.json()["treat_sc_as_revenue"] is False
    assert client_as("front_desk").get(f"/hotels/{s}/profit-guard/policies").status_code == 403


def test_rate_evidence_economics_and_expected_commission(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    owner = client_as("owner")
    ev = owner.post(f"/hotels/{s}/profit-guard/rate-evidence",
                    json={"channel": "Booking.com", "stay_date": "2026-10-10", "price": "399.00", "device": "MOBILE"})
    assert ev.status_code == 201 and ev.json()["channel"] == "BOOKING" and ev.json()["price"] == "399.00"
    assert ev.json()["emit_signal"] is True and ev.json()["source"] == "MANUAL"
    assert len(owner.get(f"/hotels/{s}/profit-guard/rate-evidence?from=2026-10-01&to=2026-10-31").json()) == 1
    ci = date(2026, 9, 1)
    rid = stays.upsert_reservation(cur, s, {
        "pms_res_id": "B1", "channel_code": "BOOKING", "guest_name": "A B", "check_in": ci,
        "check_out": ci + timedelta(days=3), "status": "CHECKED_OUT", "currency": "AED", "room_type_code": "DLX",
    }, nights=[{"stay_date": ci + timedelta(days=i), "rate_minor": 35000} for i in range(3)])["id"]
    econ = owner.get(f"/hotels/{s}/profit-guard/channel-economics?from=2026-09-01&to=2026-09-30").json()
    assert econ["channels"][0]["channel_code"] == "BOOKING" and econ["channels"][0]["commission"] == "182.52"
    assert owner.get(f"/hotels/{s}/profit-guard/channel-economics?from=2026-09-30&to=2026-09-01").status_code == 400
    ec = owner.get(f"/hotels/{s}/profit-guard/expected-commission?reservation_id={rid}").json()
    assert ec["commission"] == "182.52" and ec["basis"] == "default" and ec["calc"][0]["label"] == "Commission base"
    none = owner.get(f"/hotels/{s}/profit-guard/expected-commission?reservation_id={rid}&ota_code=DIRECT_WEB")
    assert none.status_code == 404 and none.json()["code"] == "no_terms"
