"""The demo seed's inquiry rows as the console and the guest pages read them
(QA OWN-03, GS-5): the seeded quotes open in the Quote tab and on /q, and
the website form works for the demo hotel without anyone opening Settings."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.seed import demo_uae

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-inq"
PARTS = ["core", "core_catalog", "core_stays", "ops"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def test_seeded_quotes_web_form_and_summary(cur, seeded, client):
    from clefs.modules.inquiries import channels, quotes

    cur.execute("SELECT * FROM inq_quotes WHERE hotel_slug = %s ORDER BY id", (SLUG,))
    rows = [dict(r) for r in cur.fetchall()]
    assert len(rows) >= 2
    for q in rows:
        api = quotes.to_api(dict(q))  # KeyError 'amount_minor' before the fix
        sched = q["deposit_schedule"]
        assert all(m.get("amount_minor") is not None and m.get("seq") for m in sched), sched
        assert sum(m["amount_minor"] for m in sched) == q["total_minor"]
        assert [m["purpose"] for m in sched] == ["deposit", "balance"]
        assert api["deposit_schedule"][0]["amount_value"]
    group = [q for q in rows if q["group_terms"]]
    assert group and quotes.stored_group_terms(group[0])["min_rooms_after_attrition"] == 11
    # The public page of the sent quote renders (public_view reads every milestone).
    sent = next(q for q in rows if q["status"] == "sent")
    token = quotes.public_token(sent)
    r = client.get(f"/public/inquiries/quotes/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["hotel"]["timezone"] == "Asia/Dubai"

    # The web form: connected, and the summary carries the preview URL.
    url = seeded["parts"]["ops"]["inquiry_form"]
    key = url.split("?k=", 1)[1]
    assert f"/w/inquiry/{SLUG}?k=" in url and channels.widget_matches(cur, SLUG, key) is not None
    r = client.get(f"/public/inquiries/{SLUG}/widget", params={"k": key})
    assert r.status_code == 200, r.text
