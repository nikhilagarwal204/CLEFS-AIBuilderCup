"""The quote-to-cash HTTP API (README "Quotes, payments and verification"):
permissions, static paths ahead of /{inquiry_id}, the public quote page and
accept."""

from __future__ import annotations

from clefs.modules.inquiries import quotes
from tests.modules.inquiries.test_quotes_support import CHECK_IN, demand, hotel, make_inquiry, now  # noqa: F401

BASE = "/hotels/{slug}/inquiries"


def test_build_edit_and_send_as_owner(cur, hotel, demand, client_as, run_jobs):
    inq = make_inquiry(cur, hotel)
    c = client_as("owner", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    r = c.post(f"{base}/{inq['id']}/quotes", json={})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["version"] == 1 and q["total_value"] == "1008.68" and "public_token_hash" not in q
    r = c.put(f"{base}/{inq['id']}/quotes/1", json={"edits": [{"stay_date": CHECK_IN.isoformat(), "rate_minor": 45000}]})
    assert r.status_code == 200 and r.json()["lines"][0]["source"] == "staff"
    r = c.post(f"{base}/{inq['id']}/quotes/1/send", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"]["status"] == "approved" and body["action"]["decided_via"] == "console"
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    assert c.get(f"{base}/{inq['id']}/quotes/1").json()["status"] == "sent"
    assert [x["version"] for x in c.get(f"{base}/{inq['id']}/quotes").json()] == [1]


def test_front_desk_requests_approval_only(cur, hotel, demand, client_as):
    inq = make_inquiry(cur, hotel)
    c = client_as("front_desk", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    assert c.post(f"{base}/{inq['id']}/quotes", json={}).status_code == 201
    r = c.post(f"{base}/{inq['id']}/quotes/1/send", json={})
    assert r.json()["action"]["status"] == "proposed" and r.json()["quote"]["status"] == "awaiting_approval"
    # Below the floor needs inquiries.approve.
    r = c.put(f"{base}/{inq['id']}/quotes/1", json={"edits": [{"stay_date": CHECK_IN.isoformat(), "rate_minor": 100}]})
    assert r.status_code == 403 and r.json()["code"] == "below_floor_needs_approval"


def test_static_paths_are_not_ids(cur, hotel, demand, client_as):
    c = client_as("owner", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    r = c.get(f"{base}/pipeline")
    assert r.status_code == 200 and r.json()["label"] == "est."
    assert c.get(f"{base}/match-reviews").json() == []
    assert c.get(f"{base}/quote-settings").json()["group_attrition_pct"] == "80"
    assert len(c.get(f"{base}/templates").json()) == 4
    r = c.post(f"{base}/payment-accounts", json={"provider": "stripe", "config": {"fee_pct": "2.9"},
                                                 "secret": {"secret_key": "sk", "webhook_secret": "wh"}})
    assert r.status_code == 201 and r.json()["webhook_path"].startswith("/webhooks/inquiries/payments/stripe/")
    listed = c.get(f"{base}/payment-accounts").json()
    assert [a["provider"] for a in listed] == ["stripe"] and "secret_enc" not in listed[0]


def test_put_settings_keeps_the_quote_settings(cur, hotel, client_as):
    c = client_as("owner", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    assert c.put(f"{base}/quote-settings", json={"group_min_rooms": 12}).status_code == 200
    assert c.put(f"{base}/settings", json={"ack_enabled": False}).status_code == 200
    assert c.get(f"{base}/quote-settings").json()["group_min_rooms"] == 12


def test_revenue_cannot_manage_accounts(cur, hotel, client_as):
    c = client_as("revenue", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    assert c.get(f"{base}/payment-accounts").status_code == 403
    assert c.get(f"{base}/pipeline").status_code == 200


def test_public_quote_page_and_accept(cur, hotel, demand, client, client_as, run_jobs):
    inq = make_inquiry(cur, hotel)
    owner = client_as("owner", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    owner.post(f"{base}/{inq['id']}/quotes", json={})
    owner.post(f"{base}/{inq['id']}/quotes/1/send", json={})
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    token = quotes.public_token(quotes.get_quote(cur, hotel.slug, inq["id"], 1))
    r = client.get(f"/public/inquiries/quotes/{token}?lang=ar")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"
    data = r.json()
    assert data["language"] == "ar" and data["taxes"]["total"] == "1008.68" and data["pay_url"] is None
    assert client.get("/public/inquiries/quotes/not-a-token").status_code == 404
    r = client.post(f"/public/inquiries/quotes/{token}/accept")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    assert client.get(f"/public/inquiries/quotes/{token}").json()["accepted"] is True


def test_payment_link_route_rejects_a_javascript_url(cur, hotel, demand, client_as, run_jobs):
    inq = make_inquiry(cur, hotel)
    c = client_as("owner", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    assert c.post(f"{base}/{inq['id']}/quotes", json={}).status_code == 201
    assert c.post(f"{base}/{inq['id']}/quotes/1/send", json={}).status_code == 200
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    r = c.post(f"{base}/{inq['id']}/payment-links",
               json={"quote_version": 1, "provider": "manual", "url": "javascript:fetch('/api/clefs/me')"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_payment_url", r.text
    cur.execute("SELECT COUNT(*) AS n FROM actions WHERE type = 'inquiries.send_payment_link'")
    assert cur.fetchone()["n"] == 0


# ─── Hand-written rows (QA OWN-03) ───


def test_legacy_deposit_schedule_and_group_terms_do_not_500(cur, hotel, demand, client, client_as, run_jobs):
    """A row whose deposit_schedule has no amount_minor (the old demo seed:
    [{"pct": 30, ...}], [{"pct": "first_night"}]) and whose group_terms lack
    the dates still lists, opens and renders publicly."""
    from clefs.db import Json

    inq = make_inquiry(cur, hotel)
    owner = client_as("owner", hotel.slug)
    base = BASE.format(slug=hotel.slug)
    owner.post(f"{base}/{inq['id']}/quotes", json={})
    owner.post(f"{base}/{inq['id']}/quotes/1/send", json={})
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    q = quotes.get_quote(cur, hotel.slug, inq["id"], 1)
    cur.execute("UPDATE inq_quotes SET deposit_schedule = %s, group_terms = %s WHERE id = %s",
                (Json([{"pct": 30, "due": "on_confirmation"}, {"pct": "first_night"}]),
                 Json({"attrition_pct": 10, "rooming_list_days": 7}), q["id"]))
    r = owner.get(f"{base}/{inq['id']}/quotes")
    assert r.status_code == 200, r.text
    (row,) = r.json()
    total = row["total_minor"]
    first, second = row["deposit_schedule"]
    assert first["amount_minor"] == round(total * 0.3) and first["label"] == "30% deposit"
    assert second["amount_minor"] == round(total / 2) and second["label"] == "First night"  # 2 nights
    assert [m["seq"] for m in row["deposit_schedule"]] == [1, 2] and row["group_terms"] is None
    token = quotes.public_token(quotes.get_quote(cur, hotel.slug, inq["id"], 1))
    pub = client.get(f"/public/inquiries/quotes/{token}")
    assert pub.status_code == 200, pub.text
    assert pub.json()["deposit_schedule"][0]["amount_value"] and pub.json()["group_terms"] is None
