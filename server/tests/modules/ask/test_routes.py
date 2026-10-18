"""HTTP API: ask, metrics, feedback, logs, saved views (re-run without the
LLM), the WhatsApp opt-in, permissions and the 30-per-hour limit."""

from __future__ import annotations

from .conftest import call, grounded_answer, q

BASE = "/hotels/test-dubai/ask"


def _ask(c, fake_ai, question="Commission last month", args=None):
    fake_ai.push([call("query_metric", **(args or q("pg.commission_aed", dims=["channel"], preset="last_month")))],
                 grounded_answer)
    r = c.post(BASE, json={"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def test_post_ask_and_feedback(seeded, client_as, fake_ai):
    c = client_as("owner")
    out = _ask(c, fake_ai)
    assert out["status"] == "answered" and out["log_id"]
    assert out["chart_spec"]["type"] == "column" and out["chart_spec"]["x"] == "channel"
    assert [r[0] for r in out["table"]["rows"]] == ["BOOKING", "EXPEDIA", "DIRECT"]
    r = c.post(f"{BASE}/{out['log_id']}/feedback", json={"value": -1, "note": "Expedia looks low"})
    assert r.status_code == 200 and r.json()["feedback"] == -1
    assert c.post(f"{BASE}/{out['log_id']}/feedback", json={"value": 3}).json()["code"] == "invalid_feedback"
    other = client_as("gm")
    assert other.post(f"{BASE}/{out['log_id']}/feedback", json={"value": 1}).status_code == 404


def test_metrics_catalog_is_role_filtered(seeded, client_as):
    owner = {m["key"] for m in client_as("owner").get(f"{BASE}/metrics").json()}
    head = {m["key"] for m in client_as("dept_head").get(f"{BASE}/metrics").json()}
    assert {"fin.gop_aed", "pg.commission_aed", "ask.adr"} <= owner
    assert not ({"fin.gop_aed", "pg.commission_aed", "ask.adr", "ask.revpar"} & head)
    assert "ask.occupancy" in head  # a ratio is visible exactly when its inputs are
    bookings = next(m for m in client_as("owner").get(f"{BASE}/metrics").json() if m["key"] == "stays.bookings")
    assert [d["key"] for d in bookings["dimensions"]] == ["channel"]  # guest_name stripped


def test_permissions(seeded, client_as, client, fake_ai):
    assert client.post(BASE, json={"question": "x"}).status_code == 401
    assert client_as("front_desk").post(BASE, json={"question": "x"}).status_code == 403
    assert client_as("staff").get(f"{BASE}/metrics").status_code == 403


def test_logs_and_team_review(seeded, client_as, fake_ai):
    fin = client_as("finance")
    out = _ask(fin, fake_ai)
    mine = fin.get(f"{BASE}/logs").json()
    assert [row["id"] for row in mine] == [out["log_id"]]
    assert fin.get(f"{BASE}/logs?all=true").status_code == 403
    assert fin.get(f"{BASE}/logs/{out['log_id']}").status_code == 200
    gm = client_as("gm")
    assert gm.get(f"{BASE}/logs").json() == []
    team = gm.get(f"{BASE}/logs?all=true").json()
    assert [row["id"] for row in team] == [out["log_id"]]
    assert gm.get(f"{BASE}/logs/{out['log_id']}").status_code == 200  # ask.manage
    assert client_as("revenue").get(f"{BASE}/logs/{out['log_id']}").status_code == 403


def test_saved_view_reruns_without_the_llm(seeded, client_as, fake_ai):
    c = client_as("owner")
    out = _ask(c, fake_ai)
    r = c.post(f"{BASE}/views", json={"title": "Commission by channel", "log_id": out["log_id"], "pinned": True})
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["query"]["metric"] == "pg.commission_aed" and view["query"]["period"]["preset"] == "last_month"
    n_calls = len(fake_ai.calls)
    run = c.post(f"{BASE}/views/{view['id']}/run").json()
    assert len(fake_ai.calls) == n_calls  # no model call
    assert run["status"] == "answered" and run["view"]["last_run_at"]
    assert run["answer_text"].startswith("OTA commission paid, 1-31 Aug 2026: AED 14,700.00 in total")
    assert [v["id"] for v in c.get(f"{BASE}/views").json()] == [view["id"]]
    assert c.patch(f"{BASE}/views/{view['id']}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert client_as("gm").post(f"{BASE}/views/{view['id']}/run").status_code == 404  # views are personal
    assert c.delete(f"{BASE}/views/{view['id']}").status_code == 204
    assert c.get(f"{BASE}/views").json() == []


def test_saved_view_from_query_is_validated(seeded, client_as):
    c = client_as("owner")
    bad = c.post(f"{BASE}/views", json={"title": "x", "query": q("stays.bookings", dims=["guest_name"])})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_view_query"
    ok = c.post(f"{BASE}/views", json={"title": "Leaks", "query": q("am.leakage_aed", dims=["amenity"], preset="wtd")})
    assert ok.status_code == 201
    run = c.post(f"{BASE}/views/{ok.json()['id']}/run").json()
    assert run["table"]["rows"] == [["Slippers", 300.0], ["Water bottle", 170.0]]
    assert "Highest: Slippers at AED 300.00" in run["answer_text"]


def test_rate_limit_thirty_per_hour(seeded, client_as, fake_ai, cur):
    from clefs import ratelimit
    from clefs.modules.ask.constants import QUESTIONS_PER_HOUR

    c = client_as("owner")
    for _ in range(QUESTIONS_PER_HOUR):
        assert ratelimit.hit(cur, f"ask:q:{c.user['id']}", limit=QUESTIONS_PER_HOUR, window_s=3600)
    r = c.post(BASE, json={"question": "Commission last month"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"


def test_whatsapp_opt_in(seeded, client_as, cur):
    owner = client_as("owner")
    r = owner.put(f"{BASE}/whatsapp", json={"opted_in": True})
    assert r.status_code == 409 and r.json()["code"] == "ask_whatsapp_no_phone"
    cur.execute("UPDATE users SET phone_e164 = '+971501112233' WHERE id = %s", (owner.user["id"],))
    r = owner.put(f"{BASE}/whatsapp", json={"opted_in": True})
    assert r.status_code == 200 and r.json()["opted_in"] is True and r.json()["eligible"] is True
    head = client_as("dept_head")
    assert head.get(f"{BASE}/whatsapp").json()["eligible"] is False
    assert head.put(f"{BASE}/whatsapp", json={"opted_in": True}).json()["code"] == "ask_whatsapp_role"
    assert owner.put(f"{BASE}/whatsapp", json={"opted_in": False}).json()["opted_in"] is False
