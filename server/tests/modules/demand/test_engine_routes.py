"""HTTP routes of the engine (README.md, engine section) and the public
demand-preview lead."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
LEAD = "/public/leads/demand-preview"


@pytest.fixture
def owner(cur, clock, client_as, hotel_uae):
    from clefs.modules.demand import bar, curated, events

    clock.freeze(NOW)
    curated.sync(cur, force=True)
    cur.execute("UPDATE hotels SET lat = 25.2697, lng = 55.3095 WHERE slug = %s", (hotel_uae.slug,))
    from clefs.core import hotels

    hotels.get_hotel(cur, hotel_uae.slug, fresh=True)
    for i in range(5):
        bar.set_bar(cur, hotel_uae, stay_date=date(2026, 10, 12) + timedelta(days=i), room_type_code="DLX",
                    bar_minor=40000, source="MANUAL")
    c = client_as("owner")
    c.base = f"/hotels/{hotel_uae.slug}/demand"
    c.event_id = events.ingest(cur, {"title": "Zephyr Widget Summit", "category": "CONFERENCE",
                                     "start": date(2026, 10, 13), "end": date(2026, 10, 15), "venue_codes": ["DWTC"],
                                     "attendance": 150000}, source="CURATED", external_id="zw:r")["event_id"]
    return c


def test_permissions(owner, client_as, hotel_uae):
    base = f"/hotels/{hotel_uae.slug}/demand"
    fd = client_as("front_desk")
    for path in ("/calendar", "/recommendations", "/top5", "/outcomes", "/ghc-checklist", "/campaigns",
                 "/engine-settings", "/guidance", "/calibration", "/class-prefs"):
        assert fd.get(base + path).status_code == 403, path
    fin = client_as("finance")
    assert fin.put(base + "/ghc-checklist", json={"items": []}).status_code == 403
    assert client_as("revenue").get(base + "/calendar").status_code == 200


def test_recompute_calendar_day_and_decide(owner):
    r = owner.put(f"{owner.base}/engine-settings", json={"corridor_tags": ["deira_burdubai"]})
    assert r.status_code == 200 and r.json()["corridor_tags"] == ["DEIRA_BURDUBAI"]
    r = owner.post(f"{owner.base}/recompute")
    assert r.status_code == 200, r.text
    cal = owner.get(f"{owner.base}/calendar?days=30").json()
    assert len(cal["cells"]) == 30 and cal["comp_label"] == "Indicative"
    cell = {c["stay_date"]: c for c in cal["cells"]}["2026-10-13"]
    assert cell["events"][0]["title"] == "Zephyr Widget Summit" and cell["demand_index"] > 50
    assert owner.get(f"{owner.base}/calendar?days=45").json()["code"] == "invalid_days"
    day = owner.get(f"{owner.base}/days/2026-10-13").json()
    assert day["events"][0]["relevance"] > 0 and day["pace"]["own"] == [] and day["comp"]["label"] == "Indicative"
    recs = owner.get(f"{owner.base}/recommendations?status=PROPOSED").json()
    assert recs, "a strong nearby show above BAR should produce a proposal"
    rid = recs[0]["id"]
    bad = owner.post(f"{owner.base}/recommendations/{rid}/decide", json={"verb": "adjust", "recommended": "9999"})
    assert bad.status_code == 400 and bad.json()["code"] == "rate_out_of_bounds"
    ok = owner.post(f"{owner.base}/recommendations/{rid}/decide", json={"verb": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert owner.get(f"{owner.base}/recommendations/{rid}").json()["facts"]["stay_date"] == recs[0]["stay_date"]
    assert owner.get(f"{owner.base}/outcomes").json()["actionability"]["approved"] == 1
    assert "items" in owner.get(f"{owner.base}/top5").json()
    assert owner.get(f"{owner.base}/recommendations?status=NOPE").json()["code"] == "invalid_status"


def test_dismiss_and_class_mute(owner):
    r = owner.post(f"{owner.base}/events/{owner.event_id}/dismiss", json={"reason": "not our guests", "scope": "class"})
    assert r.status_code == 200 and r.json()["dismissed"]
    prefs = owner.get(f"{owner.base}/class-prefs").json()
    assert prefs[0]["class_key"] == "CONFERENCE" and prefs[0]["dismissed"] == 1 and prefs[0]["weight"] == 0.5
    assert owner.put(f"{owner.base}/class-prefs/conference", json={"muted": True}).json()["weight"] == 0.0
    assert owner.put(f"{owner.base}/class-prefs/nope", json={"muted": True}).json()["code"] == "invalid_class"
    assert owner.delete(f"{owner.base}/events/{owner.event_id}/dismiss").status_code == 200
    assert owner.post(f"{owner.base}/events/{owner.event_id}/dismiss", json={"scope": "planet"}).status_code == 422


def test_campaigns_guidance_and_ghc(owner, cur):
    r = owner.post(f"{owner.base}/campaigns", json={"event_id": owner.event_id, "channels": ["WHATSAPP", "EMAIL"]})
    assert r.status_code == 201, r.text
    drafts = r.json()
    assert [d["channel"] for d in drafts] == ["WHATSAPP", "EMAIL"] and drafts[0]["export_only"]
    csv = owner.get(f"{owner.base}/campaigns/{drafts[0]['id']}/export.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert owner.get(f"{owner.base}/campaigns").json()[-1]["status"] == "EXPORTED"
    # The export's consent re-check counts land on the audit row (security-privacy-3).
    cur.execute("SELECT meta FROM audit_log WHERE action = 'demand.campaign.export' AND entity_id = %s",
                (str(drafts[0]["id"]),))
    meta = cur.fetchone()["meta"]
    assert set(meta) == {"exported_count", "dropped_count", "recheck"}
    p = owner.patch(f"{owner.base}/campaigns/{drafts[1]['id']}", json={"send_at": "2026-10-01T19:30:00+04:00"})
    assert p.json()["suggested_send_at"] == "2026-10-02T09:00:00+04:00"
    g = owner.post(f"{owner.base}/events/{owner.event_id}/guidance")
    assert g.status_code == 200 and {x["kind"] for x in g.json()["guidance"]} >= {"STAFFING", "KB_UPDATE"}
    cl = owner.get(f"{owner.base}/ghc-checklist").json()
    assert cl["open_items"] == 5
    cl = owner.put(f"{owner.base}/ghc-checklist", json={"items": [{"item_code": "FREE_BOOKING_LINKS",
                                                                     "status": "DONE"}]}).json()
    assert cl["open_items"] == 4


def _lead(**kw):
    body = {"hotel_name": "Harbour Inn", "city": "Dubai", "area": "Deira", "rooms": 60, "name": "Sam",
            "email": "sam@example.test", "consent_processing": True, "consent_transfer_abroad": True,
            "consent_contact_email": True}
    body.update(kw)
    return body


def test_public_lead(owner, client, cur, outbox, monkeypatch):
    monkeypatch.setenv("FOUNDER_NOTIFY_EMAIL", "founder@example.test")
    r = client.post(LEAD, json=_lead())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["ok"] and body["preview"]["area"]["label"] == "Deira"
    assert body["preview"]["events"][0]["title"] == "Zephyr Widget Summit"
    row = cur.execute("SELECT * FROM dr_leads") or cur.fetchone()
    assert row["email_enc"] and b"sam@" not in bytes(row["email_enc"]) and row["consent_contact_email"]
    mails = outbox.captured("email")
    assert mails and "sam@example.test" not in str(mails[-1])
    # Consents and contacts are checked; a honeypot stores nothing.
    assert client.post(LEAD, json=_lead(consent_transfer_abroad=False)).json()["code"] == "consent_required"
    assert client.post(LEAD, json=_lead(consent_contact_email=False)).json()["code"] == "contact_consent_required"
    assert client.post(LEAD, json=_lead(email=None)).json()["code"] == "contact_required"
    assert client.post(LEAD, json=_lead(website="http://spam")).json()["lead_id"] is None
    # 3 a day per IP: two more succeed, the next is 429.
    assert client.post(LEAD, json=_lead()).status_code == 202
    assert client.post(LEAD, json=_lead()).status_code == 202
    r = client.post(LEAD, json=_lead())
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
