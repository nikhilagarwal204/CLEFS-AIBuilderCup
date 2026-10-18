"""Shared helpers for the journeys tests (imported by every test_journeys_*
file), plus the template library tests."""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta, timezone

import pytest

from clefs.core import consent, privacy
from clefs.core.hotels import update_hotel
from clefs.modules.guests import identity
from clefs.modules.guests.journeys import planner, steps, templates

DUBAI = timezone(timedelta(hours=4))
PHONE = "+971501234567"
EMAIL = "guest@example.com"
_ids = itertools.count(9_000_000)


def at(y, m, d, hh=0, mm=0):
    """An instant given in Dubai wall time."""
    return datetime(y, m, d, hh, mm, tzinfo=DUBAI)


def channel(cur, slug, code):
    cur.execute(
        "INSERT INTO channels (hotel_slug, code, name, kind) VALUES (%s, %s, %s, 'other') ON CONFLICT DO NOTHING",
        (slug, code, code.title()),
    )


def make_res(cur, slug, *, code="DIRECT_WEB", check_in=date(2026, 10, 20), check_out=date(2026, 10, 23),
             status="CONFIRMED", room="101", pms=None, booked_at=None, segment=None, chain=None, language="en",
             checked_in_at=None, checked_out_at=None, promo_code=None, utm=None):
    channel(cur, slug, code)
    cur.execute(
        """
        INSERT INTO reservations (hotel_slug, pms_res_id, channel_code, check_in, check_out, nights, status, currency,
                                  room_number, guest_language, booked_at, segment, stay_chain_id, checked_in_at,
                                  checked_out_at, promo_code, utm, guest_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'AED', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SMITH, John') RETURNING id
        """,
        (slug, pms or f"R{next(_ids)}", code, check_in, check_out, (check_out - check_in).days, status, room, language,
         booked_at or at(2026, 9, 1, 10), segment, chain, checked_in_at, checked_out_at, promo_code,
         privacy_json(utm)),
    )
    return cur.fetchone()["id"]


def privacy_json(v):
    from clefs.db import Json

    return Json(v) if v is not None else None


def set_res(cur, rid, **cols):
    sets = ", ".join(f"{k} = %s" for k in cols)
    cur.execute(f"UPDATE reservations SET {sets} WHERE id = %s", (*cols.values(), rid))


def add_contact(cur, slug, rid, value, *, kind="phone", provenance="pms_direct", alias_domain=None, usable_until=None):
    h = privacy.contact_hmac(slug, kind, value)
    cur.execute(
        """
        INSERT INTO reservation_contacts (hotel_slug, reservation_id, kind, value_enc, value_hmac, provenance,
                                          alias_domain, usable_until)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (slug, rid, kind, privacy.seal(value), h, provenance, alias_domain, usable_until),
    )
    return h


def event(cur, slug, rid, name, **changes):
    ev = {"id": next(_ids), "event": name, "hotel_slug": slug, "reservation_id": rid,
          "occurred_at": changes.pop("occurred_at", None), "changes": changes}
    return planner.handle_event(cur, ev)


def plans(cur, slug, rid, *, live=True):
    return {(p["step"], p["seq"]): p for p in planner.plans_for(cur, slug, rid, live_only=live)}


def grant(cur, slug, h, *purposes):
    for p in purposes:
        consent.record_consent(cur, slug, h.hex(), p, True, source="test", subject_type="contact",
                               text_version=identity.text_version(cur, slug, p))


def approve_templates(cur, slug, *, channels=("whatsapp", "email"), step_list=None, meta="APPROVED"):
    templates.ensure_library(cur, slug)
    cur.execute(
        "UPDATE gst_journey_templates SET approved_at = now(), meta_status = CASE WHEN channel = 'whatsapp' THEN %s END "
        "WHERE hotel_slug = %s AND channel = ANY(%s) AND step = ANY(%s)",
        (meta, slug, list(channels), list(step_list or steps.SEND_STEPS)),
    )


def enable_whatsapp(cur, slug, *, attested=True):
    update_hotel(cur, slug, {"whatsapp_phone_number_id": "1234567890"})
    if attested:
        from clefs.core.hotels import set_module_settings

        set_module_settings(cur, slug, "guests", {"wa_billing_attested_at": "2026-09-01T00:00:00+04:00"}, actor=None)


# ─── Template library ───


@pytest.mark.parametrize("step", steps.SEND_STEPS)
@pytest.mark.parametrize("lang", ["en", "ar"])
def test_library_lints_clean(step, lang):
    spec = templates.LIBRARY[step]
    r = templates.lint(spec[lang], category=steps.EXPECTED_CATEGORY[step], step=step,
                       variables=templates.variables_of(step), buttons=templates.buttons_of(step, lang))
    assert r == {"ok": True, "errors": []}


def test_lint_catches_each_rule():
    j4 = templates.variables_of("j4")
    no_close = "Hello {{1}}, see you on {{2}}. Check-in is from {{3}}. Please bring {{4}}. Directions: {{5}}."
    assert "variable_at_edge" in templates.lint(no_close, category="UTILITY", step="j4", variables=j4)["errors"]
    adj = "Hello {{1}} {{2}}, from {{3}}. Bring {{4}} and {{5}} please."
    assert "adjacent_variables" in templates.lint(adj, category="UTILITY", step="j4", variables=j4)["errors"]
    j3 = templates.variables_of("j3")
    no_hotel = [v for v in j3 if v["source"] != "hotel_name"]
    body = "Hi {{1}}, arriving {{2}}: {{3}} for {{4}}. Reply STOP to opt out."
    assert "marketing_no_hotel_name" in templates.lint(body, category="MARKETING", step="j3", variables=no_hotel)["errors"]
    review = templates.LIBRARY["j3"]["en"].replace("Reply YES", "Leave a review and reply YES")
    assert "review_words" in templates.lint(review, category="MARKETING", step="j3", variables=j3)["errors"]
    promo = templates.LIBRARY["j2"]["en"].replace("For planning", "Special deal")
    assert "utility_promotional" in templates.lint(promo, category="UTILITY", step="j2",
                                                   variables=templates.variables_of("j2"))["errors"]
    assert "em_dash" in templates.lint("Hi {{1}} \u2014 ok.", category="UTILITY", step="j2",
                                       variables=templates.variables_of("j2")[:1])["errors"]
    long_qr = [{"type": "QUICK_REPLY", "text": "x" * 26}]
    assert "quick_reply_too_long" in templates.lint("Hi {{1}}, ok.", category="UTILITY", step="j5",
                                                    variables=templates.variables_of("j5")[:1], buttons=long_qr)["errors"]
    assert templates.lint_values(["20% discount"], category="UTILITY", step="j2") == ["utility_promotional"]
    assert templates.lint_values(["rate us"], category="MARKETING", step="j8") == ["review_words"]


def test_seed_is_idempotent_and_unapproved(cur, hotel_uae):
    n = templates.ensure_library(cur, hotel_uae.slug)
    assert n == len(steps.SEND_STEPS) * 2 * 2
    assert templates.ensure_library(cur, hotel_uae.slug) == 0
    assert templates.pending_approval(cur, hotel_uae.slug) == n
    assert templates.approved_template(cur, hotel_uae.slug, "j1", "email", "en") is None


def test_whatsapp_needs_owner_and_meta_approval(cur, hotel_uae):
    s = hotel_uae.slug
    approve_templates(cur, s, channels=("whatsapp",), meta="PENDING")
    assert templates.approved_template(cur, s, "j1", "whatsapp", "ar") is None
    approve_templates(cur, s, channels=("whatsapp",), meta="APPROVED")
    t = templates.approved_template(cur, s, "j1", "whatsapp", "ar")
    assert t["language"] == "ar" and templates.template_ref(t) == "gst_j1_confirm_v1@1"


def make_offer(cur, slug, step, *, title="Airport transfer", price_minor=15000, discount_pct=None, code_prefix="BACK",
               valid_from=date(2026, 1, 1), valid_to=date(2027, 12, 31), approved=True):
    cur.execute(
        """
        INSERT INTO gst_journey_offers (hotel_slug, step, title_en, title_ar, price_minor, discount_pct, currency,
                                        code_prefix, valid_from, valid_to, approved_at, active)
        VALUES (%s, %s, %s, %s, %s, %s, 'AED', %s, %s, %s, %s, TRUE) RETURNING *
        """,
        (slug, step, title, None, price_minor, discount_pct, code_prefix, valid_from, valid_to,
         datetime(2026, 1, 1, tzinfo=timezone.utc) if approved else None),
    )
    return dict(cur.fetchone())


def sends(cur, slug, *, category=None, channel=None):
    """Journey step messages in the outbox (purpose guests.journey.<step>)."""
    cur.execute(
        "SELECT * FROM outbound_messages WHERE hotel_slug = %s AND purpose LIKE 'guests.journey.%%' "
        "AND status <> 'cancelled' ORDER BY id",
        (slug,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if (category is None or r["category"] == category)
            and (channel is None or r["channel"] == channel)]


_wamids = itertools.count(1)


def inbound(slug_unused, sender, text=None, *, context=None, button=None, button_text=None, phone_number_id="1234567890"):
    """One guest WhatsApp message through the real webhook processor (its
    db() blocks are savepoints of the test transaction)."""
    from clefs import notify
    from clefs.core import clock as clock_mod

    msg = {"from": sender.lstrip("+"), "id": f"wamid.in{next(_wamids)}",
           "timestamp": str(int(clock_mod.now().timestamp()))}
    if button:
        msg.update(type="button", button={"payload": button, "text": button_text or "button"})
    else:
        msg.update(type="text", text={"body": text or ""})
    if context:
        msg["context"] = {"id": context}
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "97140000000",
                                                      "phone_number_id": phone_number_id},
        "contacts": [], "messages": [msg]}}]}]}
    return notify.process_whatsapp(payload)


def in_house_j5(cur, slug, clock, *, phone=PHONE, code="DIRECT_WEB"):
    """An in-house stay whose J5 day-1 check went out on WhatsApp."""
    from clefs.modules.guests.journeys import dispatch

    enable_whatsapp(cur, slug)
    approve_templates(cur, slug)
    clock.freeze(at(2026, 10, 20, 15))
    rid = make_res(cur, slug, code=code, status="IN_HOUSE", checked_in_at=at(2026, 10, 20, 15), room="501")
    h = add_contact(cur, slug, rid, phone)
    grant(cur, slug, h, "whatsapp_updates")
    event(cur, slug, rid, "checked_in", status=["CONFIRMED", "IN_HOUSE"])
    clock.freeze(at(2026, 10, 21, 9, 5))
    dispatch.dispatch_hotel(cur, slug)
    return rid, h, plans(cur, slug, rid)[("j5", 1)]
