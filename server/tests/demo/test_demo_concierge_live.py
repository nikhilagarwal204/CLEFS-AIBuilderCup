"""The demo hotel's guest concierge is live after a seed (QA GS-1): before,
cc_live had no row for it, so every guest message and quick chip on its QR
pages got "call the front desk" and filed no staff request."""

from __future__ import annotations

from datetime import date

from clefs.seed import demo_uae
from clefs.seed.demo_uae import concierge_live

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-cc-live"
PARTS = ["core", "core_catalog", "concierge_live"]
GRANTS = {"ai_processing": True, "transfer_abroad": True, "recording": True}


def _seed(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, password="demo-pass-123")


def test_seed_switches_the_concierge_on_and_reseed_keeps_it(cur):
    out = _seed(cur)
    assert out["parts"]["concierge_live"] == {"concierge_live": 1}
    cur.execute("SELECT live, source FROM cc_live WHERE hotel_slug = %s", (SLUG,))
    assert dict(cur.fetchone()) == {"live": True, "source": "demo"}
    # A reseed wipes cc_live with the rest of the hotel: the part puts it back.
    _seed(cur)
    cur.execute("SELECT live FROM cc_live WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["live"] is True
    assert concierge_live.concierge_live_checks(cur, SLUG) == [("concierge live for guests", True, "source=demo")]


def test_seeded_demo_page_is_live_for_guests(client, cur):
    from clefs.core import rooms
    from clefs.modules.concierge import sessions

    _seed(cur)
    room = "107"
    k = rooms.qr_key(SLUG, room)
    ctx = client.get(f"/public/stay/{SLUG}/{room}/context", params={"k": k})
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["features"]["assistant"] is True
    sessions.set_mint_transport(lambda body: {"value": "ek_demo_live", "expires_at": 0})
    try:
        r = client.post("/public/concierge/sessions",
                        json={"hotel": SLUG, "target": {"room": room}, "lang": "en", "mode": "voice", "consents": GRANTS})
    finally:
        sessions.set_mint_transport(None)
    assert r.status_code == 200, r.text
