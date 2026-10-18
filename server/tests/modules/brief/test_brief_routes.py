"""HTTP routes under /hotels/{slug}/brief and the magic-link decision flow."""

from __future__ import annotations

from datetime import datetime, timezone

from clefs.core import actions as A


def test_today_preview_then_stored_run(client_as, hotel_uae, frozen, sig, outbox):
    sig("leak", section="money_lost", aed=1020, hours=10)
    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/brief"
    r = owner.get(f"{base}/today")
    assert r.status_code == 200 and r.json()["preview"] is True and r.json()["items"][0]["title"] == "leak"
    r = owner.post(f"{base}/preview", json={"cadence": "weekly"})
    assert r.status_code == 200 and r.json()["cadence"] == "weekly"
    r = owner.post(f"{base}/send-now", json={"cadence": "daily", "only_me": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and [x["user_id"] for x in body["sent_to"]] == [owner.user["id"]]
    today = owner.get(f"{base}/today").json()
    assert today["preview"] is False and today["id"] == body["brief_id"]
    assert owner.get(f"{base}/runs/{body['brief_id']}").json()["items"][0]["title"] == "leak"
    assert owner.get(f"{base}/2026-10-01").json()["id"] == body["brief_id"]
    assert owner.get(f"{base}/2026-09-01").status_code == 404
    assert [x["id"] for x in owner.get(f"{base}/runs").json()] == [body["brief_id"]]


def test_permissions(client_as, hotel_uae, frozen):
    base = f"/hotels/{hotel_uae.slug}/brief"
    fin = client_as("finance")
    assert fin.get(f"{base}/today").status_code == 200
    assert fin.post(f"{base}/send-now", json={}).status_code == 403
    assert fin.put(f"{base}/settings", json={"top_n": 3}).status_code == 403
    assert client_as("front_desk").get(f"{base}/today").status_code == 403


def test_settings_roundtrip(client_as, hotel_uae, frozen):
    base = f"/hotels/{hotel_uae.slug}/brief"
    owner = client_as("owner")
    s = owner.get(f"{base}/settings").json()
    assert s["brief_time_local"] == "07:00" and s["timezone"] == "Asia/Dubai" and s["daily_enabled"] is True
    assert [x["user_id"] for x in s["recipients"]] == [owner.user["id"]]
    r = owner.put(f"{base}/settings", json={"brief_time_local": "08:15", "weekly_enabled": False,
                                            "recipient_user_ids": [owner.user["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["brief_time_local"] == "08:15" and r.json()["weekly_enabled"] is False
    assert owner.put(f"{base}/settings", json={"recipient_user_ids": [999999]}).json()["code"] == "invalid_recipient"
    assert owner.put(f"{base}/settings", json={"brief_time_local": "25:00"}).status_code == 422


def test_actions_list_and_timeline(client_as, cur, hotel_uae, frozen, act, outbox):
    from clefs.modules.brief import brief, escalate

    owner = client_as("owner")
    a = act("Restock", due_hours=1)
    A.decide(cur, a["id"], "approve", actor=None, hotel_slug=hotel_uae.slug)
    brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    frozen.advance(hours=2)
    escalate.run(cur, hotel_uae.slug)
    base = f"/hotels/{hotel_uae.slug}/brief"
    rows = owner.get(f"{base}/actions?overdue=true").json()
    assert [x["id"] for x in rows] == [a["id"]] and rows[0]["overdue"] and rows[0]["escalation_level"] == 1
    tl = owner.get(f"{base}/actions/{a['id']}/timeline").json()
    kinds = [x["kind"] for x in tl["timeline"]]
    assert kinds[0] == "event" and "escalation" in kinds
    assert tl["action"]["id"] == a["id"]


def test_weekly_report_routes(client_as, hotel_uae, clock):
    clock.freeze(datetime(2026, 10, 6, 4, tzinfo=timezone.utc))
    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/brief/reports/weekly"
    r = owner.get(f"{base}/2026-W40")
    assert r.status_code == 200 and r.json()["period_key"] == "2026-W40"
    pdf = owner.get(f"{base}/2026-W40/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert owner.get(f"{base}/2026-W41").json()["code"] == "week_not_over"
    assert owner.get(f"{base}/nonsense").json()["code"] == "invalid_week"
    assert [x["period_key"] for x in owner.get(base).json()] == ["2026-W40"]


def test_autonomy_block_route(client_as, hotel_uae, frozen):
    r = client_as("gm").get(f"/hotels/{hotel_uae.slug}/brief/autonomy")
    assert r.status_code == 200 and r.json() == {"promotions": [], "demotions": []}


def test_magic_link_decides_only_surfaced_actions(app, cur, hotel_uae, frozen, act, sig, owner, outbox):
    from fastapi.testclient import TestClient

    from clefs.modules.brief import brief

    s = sig("leak", section="money_lost", aed=1020, hours=10)
    surfaced = act("Dispute", signal_id=s["id"])
    other = act("Something else")
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    run = {**run, "overdue": [], "items": [i for i in run["items"] if i["action_id"] == surfaced["id"]]}
    token = brief.mint_link(cur, run, owner["id"]).rsplit("/", 1)[-1]
    c = TestClient(app)
    r = c.post("/auth/magic/exchange", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["next_path"] == f"/app/{hotel_uae.slug}/brief?run={run['id']}"
    assert c.get(f"/hotels/{hotel_uae.slug}/brief/runs/{run['id']}").status_code == 200
    ok = c.post(f"/hotels/{hotel_uae.slug}/actions/{surfaced['id']}/decide", json={"decision": "approve"})
    assert ok.status_code == 200, ok.text
    no = c.post(f"/hotels/{hotel_uae.slug}/actions/{other['id']}/decide", json={"decision": "approve"})
    assert no.status_code == 403


# ─── Signal-only items (review completeness-05, QA OWN-02) ───


def test_signal_only_item_can_be_proposed_assigned_snoozed_and_dismissed(client_as, cur, hotel_uae, frozen, sig):
    from clefs.core import signals as S

    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/brief/signals"
    rec = S.RecommendedAction(type="core.task", label="Charge the VCC", owner_role="finance")
    s1 = sig("vcc", section="money_lost", aed=3637.10, hours=10, recommended_action=rec)
    r = owner.post(f"{base}/{s1['id']}/decide", json={"decision": "propose"})
    assert r.status_code == 200, r.text
    a = r.json()["action"]
    assert a["title"] == "Charge the VCC" and a["signal_id"] == s1["id"] and a["expected_minor"] == 363710
    # The item now carries the action, so the card decides it through core.
    item = owner.get(f"/hotels/{hotel_uae.slug}/brief/today").json()["items"][0]
    assert item["action_id"] == a["id"]
    # A second propose returns the live action rather than a duplicate.
    assert owner.post(f"{base}/{s1['id']}/decide", json={"decision": "propose"}).json()["action"]["id"] == a["id"]

    s2 = sig("l1", section="service_risk", hours=-5)
    r = owner.post(f"{base}/{s2['id']}/decide", json={"decision": "assign", "assignee_user_id": owner.user["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["action"]["status"] == "assigned" and r.json()["action"]["type"] == "core.task"
    assert owner.post(f"{base}/{s2['id']}/decide", json={"decision": "assign"}).status_code in (400, 409)

    s3 = sig("snz", section="service_risk", hours=5)
    r = owner.post(f"{base}/{s3['id']}/decide", json={"decision": "snooze"})
    assert r.status_code == 200 and r.json()["signal"]["status"] == "snoozed"
    assert owner.post(f"{base}/{s3['id']}/decide", json={"decision": "dismiss"}).json()["code"] == "signal_closed"

    s4 = sig("dis", section="service_risk", hours=5)
    fin = client_as("finance")
    assert fin.post(f"{base}/{s4['id']}/decide", json={"decision": "dismiss"}).status_code == 403
    r = owner.post(f"{base}/{s4['id']}/decide", json={"decision": "dismiss", "note": "handled offline"})
    assert r.status_code == 200 and r.json()["signal"]["status"] == "ignored"
    assert owner.post(f"{base}/999999/decide", json={"decision": "snooze"}).status_code == 404
    other = client_as("front_desk")
    assert other.post(f"{base}/{s4['id']}/decide", json={"decision": "snooze"}).status_code == 403
    # The item's own role may close it; another role may not.
    s5 = sig("fin", section="service_risk", hours=5, owner_role="finance")
    assert client_as("revenue").post(f"{base}/{s5['id']}/decide", json={"decision": "snooze"}).status_code == 403
    assert fin.post(f"{base}/{s5['id']}/decide", json={"decision": "snooze"}).status_code == 200
