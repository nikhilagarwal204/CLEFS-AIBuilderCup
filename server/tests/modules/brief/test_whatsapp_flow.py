"""WhatsApp brief: the template send, button taps through the real webhook
dispatch, nonces (valid, replayed, other recipient), the list and reply-button
flows, Adjust sessions with bounds, dispute documents and STOP/START."""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from clefs import notify
from clefs.auth import tokens
from clefs.core import actions as A
from clefs.modules.brief import api as brief_api
from clefs.modules.brief import nonce, templates, whatsapp

OWNER_PHONE = "+971501234567"
GM_PHONE = "+971509876543"
PLATFORM = "PNID-PLATFORM"
_ids = itertools.count(1)


@pytest.fixture
def env(monkeypatch):
    from clefs.settings import reload_settings

    def _set(**values):
        for k, v in values.items():
            monkeypatch.setenv(k, str(v))
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def wa_ready(cur, env, outbox):
    """The platform number configured and owner_brief_v1 APPROVED as UTILITY."""
    env(WA_PLATFORM_PHONE_NUMBER_ID=PLATFORM, NOTIFY_MODE="capture")
    for name in (templates.OWNER_BRIEF, templates.WEEKLY_REPORT):
        templates.upsert(cur, None, name, "en", category_expected="UTILITY", category="UTILITY", status="APPROVED")
    return outbox


@pytest.fixture
def owner_p(cur, owner):
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", (OWNER_PHONE, owner["id"]))
    return owner


def _only_owner(cur, hotel, owner):
    from clefs.core.hotels import set_module_settings

    set_module_settings(cur, hotel.slug, "brief", {"recipient_user_ids": [owner["id"]]}, actor=A.SYSTEM)


def tap(payload: str, *, sender: str = OWNER_PHONE, kind: str = "button", text: str | None = None) -> dict:
    """Deliver one inbound message through the foundation's webhook dispatch."""
    frm = sender.lstrip("+")
    from clefs.core import clock

    m: dict = {"from": frm, "id": f"wamid.in.{next(_ids)}", "timestamp": str(int(clock.now().timestamp()))}
    if kind == "button":
        m.update(type="button", button={"payload": payload, "text": "tap"})
    elif kind == "text":
        m.update(type="text", text={"body": text})
    else:
        m.update(type="interactive", interactive={"type": kind, kind: {"id": payload, "title": "tap"}})
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PLATFORM}, "messages": [m]}}]}]}
    return notify.process_whatsapp(body)


def sent_wa(outbox, kind: str | None = None) -> list[dict]:
    """Every WhatsApp payload queued, in order. Read from the outbox table,
    not the capture list: notify paces 6 s per recipient, so a reply right
    after the template waits in the queue (still with its final payload)."""
    from clefs import db

    rows = db.fetch_all("SELECT payload FROM outbound_messages WHERE channel = 'whatsapp' ORDER BY id")
    return [r["payload"] for r in rows if kind is None or r["payload"].get("type") == kind]


def last_text(outbox) -> str:
    return sent_wa(outbox, "text")[-1]["text"]["body"]


def template_buttons(p: dict) -> list[str]:
    comps = p["template"]["components"]
    return [c["parameters"][0].get("payload") or c["parameters"][0].get("text") for c in comps if c["type"] == "button"]


def interactive_ids(p: dict) -> list[str]:
    act = p["interactive"]["action"]
    if "buttons" in act:
        return [b["reply"]["id"] for b in act["buttons"]]
    return [r["id"] for s in act["sections"] for r in s["rows"]]


@pytest.fixture
def pushed(cur, hotel_uae, frozen, wa_ready, owner_p, sig, act):
    """A daily brief with one proposed action, sent on WhatsApp to the owner."""
    _only_owner(cur, hotel_uae, owner_p)
    s = sig("leak", section="money_lost", aed=1020, hours=10, title="Booking no-show commission")
    a = act("Charge the no-show", signal_id=s["id"], aed=1020, due_hours=10)
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["sent_to"][0]["channel"] == "whatsapp", out
    p = sent_wa(wa_ready, "template")[-1]
    return {"action": a, "template": p, "buttons": template_buttons(p), "run_id": out["brief_id"]}


# ─── The template ───


def test_brief_goes_out_as_the_utility_template_with_signed_buttons(cur, pushed, wa_ready):
    p = pushed["template"]
    assert p["template"]["name"] == "owner_brief_v1"
    body = next(c for c in p["template"]["components"] if c["type"] == "body")["parameters"]
    assert len(body) == 7 and all(x["text"] and "\n" not in x["text"] for x in body)
    approve, review, url = pushed["buttons"]
    assert tokens.verify_ref(approve).startswith("brief:2026-10-01:1:approve:")
    assert tokens.verify_ref(review).startswith("brief:2026-10-01:list:")
    assert url and "/" not in url  # the magic-link token only (the template holds {web}/brief/)
    cur.execute("SELECT status, outbox_id FROM brf_deliveries WHERE run_id = %s", (pushed["run_id"],))
    d = cur.fetchone()
    assert d["status"] == "pending" and d["outbox_id"]


def test_unapproved_or_recategorised_template_falls_to_email(cur, hotel_uae, frozen, wa_ready, owner_p, sig):
    _only_owner(cur, hotel_uae, owner_p)
    sig("leak", section="money_lost", aed=500, hours=10)
    templates.upsert(cur, None, templates.OWNER_BRIEF, "en", category="MARKETING", status="APPROVED")
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["sent_to"][0]["channel"] == "email"
    cur.execute("SELECT delivery FROM brf_runs WHERE id = %s", (out["brief_id"],))
    assert cur.fetchone()["delivery"][0]["attempts"][0]["status"] == "template_recategorised"


# ─── Nonces ───


def test_approve_tap_decides_once_and_replay_is_refused(cur, pushed, wa_ready):
    approve = pushed["buttons"][0]
    tap(approve)
    a = A.get_action(cur, pushed["action"]["hotel_slug"], pushed["action"]["id"])
    assert a["status"] == "approved"
    assert "Approved #1" in last_text(wa_ready)
    cur.execute("SELECT via, nonce FROM action_events WHERE action_id = %s AND event = 'approve'", (a["id"],))
    ev = cur.fetchone()
    assert ev["via"] == "whatsapp" and ev["nonce"]
    tap(approve)  # a second tap (a new wamid) with the same button
    assert last_text(wa_ready) == whatsapp.NONCE_TEXT["replayed"]


def test_forwarded_button_from_another_member_is_refused(cur, pushed, wa_ready, gm):
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", (GM_PHONE, gm["id"]))
    tap(pushed["buttons"][0], sender=GM_PHONE)
    assert last_text(wa_ready) == whatsapp.NONCE_TEXT["other_recipient"]
    assert A.get_action(cur, pushed["action"]["hotel_slug"], pushed["action"]["id"])["status"] == "proposed"


def test_nonce_formula_binds_recipient_and_expiry(cur, hotel_uae, frozen, owner, gm):
    n = nonce.mint(cur, hotel_slug=hotel_uae.slug, user_id=owner["id"], verb="approve", run_id=None, action_id=None)
    assert len(n) == 12 and n.isalnum() and n.upper() == n
    assert nonce.verify(cur, n, user_id=owner["id"], verb="approve").user_id == owner["id"]
    with pytest.raises(nonce.NonceError) as e:
        nonce.verify(cur, n, user_id=gm["id"])
    assert e.value.code == "other_recipient"
    with pytest.raises(nonce.NonceError) as e:
        nonce.verify(cur, n, user_id=owner["id"], verb="ignore")
    assert e.value.code == "mismatch"
    frozen.advance(hours=73)
    with pytest.raises(nonce.NonceError) as e:
        nonce.verify(cur, n, user_id=owner["id"])
    assert e.value.code == "expired"
    with pytest.raises(nonce.NonceError) as e:
        nonce.verify(cur, "AAAAAAAAAAAA", user_id=owner["id"])
    assert e.value.code == "unknown"


def test_unsigned_payload_is_not_ours(cur, pushed, wa_ready):
    raw = tokens.verify_ref(pushed["buttons"][0])  # the ref without its signature tag
    out = tap(raw)
    assert out.get("unhandled", 0) >= 1 or out.get("handled", 0) == 0
    assert A.get_action(cur, pushed["action"]["hotel_slug"], pushed["action"]["id"])["status"] == "proposed"


# ─── List, reply buttons, More ───


def _open_item(pushed, outbox) -> list[str]:
    """Review items -> the list -> the first row -> [Approve, Assign, More] ids."""
    tap(pushed["buttons"][1])
    lst = sent_wa(outbox, "interactive")[-1]
    assert lst["interactive"]["type"] == "list"
    rows = lst["interactive"]["action"]["sections"][0]["rows"]
    assert 1 <= len(rows) <= 5 and all(len(r["title"]) <= 24 and len(r.get("description", "")) <= 72 for r in rows)
    assert tokens.verify_ref(rows[0]["id"]).startswith(f"act:{pushed['action']['id']}:open:")
    tap(rows[0]["id"], kind="list_reply")
    btns = sent_wa(outbox, "interactive")[-1]
    assert btns["interactive"]["type"] == "button"
    assert [b["reply"]["title"] for b in btns["interactive"]["action"]["buttons"]] == ["Approve", "Assign", "More"]
    return interactive_ids(btns)


def _more(ids, outbox) -> dict[str, str]:
    tap(ids[2], kind="button_reply")
    more = sent_wa(outbox, "interactive")[-1]
    return {tokens.verify_ref(i).split(":")[2]: i for i in interactive_ids(more)}


def test_list_then_buttons_then_approve(cur, pushed, wa_ready):
    ids = _open_item(pushed, wa_ready)
    tap(ids[0], kind="button_reply")
    assert A.get_action(cur, "test-dubai", pushed["action"]["id"])["status"] == "approved"


def test_more_menu_snooze_and_ignore_with_reason(cur, pushed, wa_ready):
    ids = _open_item(pushed, wa_ready)
    menu = _more(ids, wa_ready)
    assert set(menu) == {"adjust", "dispute", "remind", "snooze1", "snooze7", "ignore"}
    tap(menu["snooze7"], kind="list_reply")
    a = A.get_action(cur, "test-dubai", pushed["action"]["id"])
    assert a["status"] == "snoozed" and "7 days" in last_text(wa_ready)
    tap(menu["ignore"], kind="list_reply")
    reasons = sent_wa(wa_ready, "interactive")[-1]
    by_code = {tokens.verify_ref(i).split(":")[2]: i for i in interactive_ids(reasons)}
    tap(by_code["ignore-wrong"], kind="list_reply")
    a = A.get_action(cur, "test-dubai", pushed["action"]["id"])
    assert a["status"] == "rejected"
    cur.execute("SELECT data FROM action_events WHERE action_id = %s AND event = 'ignore'", (a["id"],))
    assert "Not accurate" in str(cur.fetchone()["data"])


def test_assign_picks_a_member(cur, pushed, wa_ready, gm):
    ids = _open_item(pushed, wa_ready)
    tap(ids[1], kind="button_reply")
    people = sent_wa(wa_ready, "interactive")[-1]
    pick = next(i for i in interactive_ids(people) if tokens.verify_ref(i).split(":")[2] == f"assign-u{gm['id']}")
    tap(pick, kind="list_reply")
    a = A.get_action(cur, "test-dubai", pushed["action"]["id"])
    assert a["status"] == "assigned" and a["assignee_user_id"] == gm["id"]
    assert "Gina GM" in last_text(wa_ready)


def test_remind_nudges_the_holder(cur, pushed, wa_ready, gm):
    A.decide(cur, pushed["action"]["id"], "assign", actor=A.SYSTEM, assignee_user_id=gm["id"], hotel_slug="test-dubai")
    ids = _open_item(pushed, wa_ready)
    tap(_more(ids, wa_ready)["remind"], kind="list_reply")
    assert "Reminder sent to Gina GM" in last_text(wa_ready)
    from clefs import db

    assert db.fetch_one("SELECT 1 AS x FROM outbound_messages WHERE purpose = 'brief.remind'")


def test_dispute_sends_the_packet_as_a_document(cur, pushed, wa_ready):
    ids = _open_item(pushed, wa_ready)
    tap(_more(ids, wa_ready)["dispute"], kind="list_reply")
    a = A.get_action(cur, "test-dubai", pushed["action"]["id"])
    assert a["status"] in ("approved", "submitted")
    doc = sent_wa(wa_ready, "document")[-1]
    assert doc["document"]["filename"].endswith(".pdf")
    cur.execute("SELECT purpose, content_type FROM files WHERE purpose = 'brief.dispute_packet'")
    assert cur.fetchone()["content_type"] == "application/pdf"


# ─── Adjust session ───


@pytest.fixture
def rate_pushed(cur, hotel_uae, frozen, wa_ready, owner_p, sig, act):
    _only_owner(cur, hotel_uae, owner_p)
    s = sig("rate", section="money_available", aed=2400, hours=20, title="Raise Friday BAR")
    a = act("Raise Friday BAR", signal_id=s["id"], aed=2400, due_hours=20,
            params={"rate_minor": 45000, "floor_minor": 38000, "ceiling_minor": 52000})
    brief_api.push_now(cur, hotel_uae.slug)
    p = sent_wa(wa_ready, "template")[-1]
    return {"action": a, "buttons": template_buttons(p)}


def test_adjust_asks_for_a_bounded_rate_and_applies_it(cur, rate_pushed, wa_ready):
    ids = _open_item(rate_pushed, wa_ready)
    tap(_more(ids, wa_ready)["adjust"], kind="list_reply")
    assert last_text(wa_ready) == "Reply with a rate between AED 380 and 520."
    tap("", kind="text", text="600")
    assert last_text(wa_ready).startswith("Please reply with a number between AED 380 and 520")
    assert A.get_action(cur, "test-dubai", rate_pushed["action"]["id"])["status"] == "proposed"
    tap("", kind="text", text="AED 455")
    a = A.get_action(cur, "test-dubai", rate_pushed["action"]["id"])
    assert a["status"] == "approved" and a["params"]["rate_minor"] == 45500
    assert "Adjusted rate to AED 455" in last_text(wa_ready)
    cur.execute("SELECT count(*) AS n FROM brf_wa_sessions")
    assert cur.fetchone()["n"] == 0


def test_adjust_session_expires(cur, rate_pushed, wa_ready, frozen):
    ids = _open_item(rate_pushed, wa_ready)
    tap(_more(ids, wa_ready)["adjust"], kind="list_reply")
    frozen.advance(minutes=31)
    out = tap("", kind="text", text="450")
    assert out.get("handled", 0) == 0  # no open question: the text passes on (Ask Clefs)
    assert A.get_action(cur, "test-dubai", rate_pushed["action"]["id"])["status"] == "proposed"


def test_parse_amount_and_bounds_conventions():
    assert whatsapp.parse_amount("AED 1,020.50") == whatsapp.Decimal("1020.50")
    assert whatsapp.parse_amount("four hundred") is None and whatsapp.parse_amount("1,2,3") is None
    b = whatsapp.adjust_bounds({"params": {"adjust": {"field": "price_minor", "min_minor": 1, "max_minor": 9}}}, "AED")
    assert b["field"] == "price_minor" and b["label"] == "price"
    b = whatsapp.adjust_bounds({"params": {"amount_minor": 5, "min_amount_minor": 1, "max_amount_minor": 9}}, "AED")
    assert (b["min_minor"], b["max_minor"]) == (1, 9)
    assert whatsapp.adjust_bounds({"params": {"amount_minor": 5}}, "AED") is None


def test_adjust_edits_the_recommended_rate_not_the_current_bar():
    # QA OWN-01: dr.rate_change lists current_bar_minor first; Adjust must
    # still edit recommended_minor, and a lone baseline is never offered.
    p = {"current_bar_minor": 118500, "recommended_minor": 136000, "floor_minor": 100000, "ceiling_minor": 150000}
    b = whatsapp.adjust_bounds({"params": p}, "AED")
    assert b["field"] == "recommended_minor" and (b["min_minor"], b["max_minor"]) == (100000, 150000)
    assert whatsapp.adjust_bounds({"params": {"current_bar_minor": 5, "floor_minor": 1, "ceiling_minor": 9}}, "AED") is None
    assert whatsapp.adjust_fields({"old_price_minor": 1, "price_minor": 2, "proposed_price_minor": 3}) == [
        "proposed_price_minor", "price_minor"]


# ─── Opt-out, window ───


def test_stop_pauses_whatsapp_briefs_and_start_resumes(cur, pushed, wa_ready, hotel_uae, owner_p, sig):
    tap("", kind="text", text="STOP")
    assert "paused" in last_text(wa_ready)
    assert not whatsapp.whatsapp_allowed(cur, hotel_uae.slug, owner_p["id"])
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["sent_to"][0]["channel"] == "email"
    tap("", kind="text", text="start")
    assert whatsapp.whatsapp_allowed(cur, hotel_uae.slug, owner_p["id"])
    cur.execute("SELECT action FROM audit_log WHERE action LIKE 'brief.wa.opt_%%' ORDER BY id")
    assert [r["action"] for r in cur.fetchall()] == ["brief.wa.opt_out", "brief.wa.opt_in"]


def test_session_messages_need_the_window_templates_do_not(cur, pushed, wa_ready, frozen):
    # The brief itself is a template (no window needed); a tap opens the
    # window and the list goes as an interactive session message.
    tap(pushed["buttons"][1])
    assert sent_wa(wa_ready, "interactive")
    # 25 h later the window has closed: the same list tap is refused by
    # notify (outside_window), never sent as a free-form message.
    frozen.advance(hours=25)
    from clefs import db

    before = db.fetch_one("SELECT count(*) AS n FROM outbound_messages WHERE status = 'outside_window'")["n"]
    row = nonce.load(cur, tokens.verify_ref(pushed["buttons"][1]).split(":")[-1])
    ctx = notify.InboundCtx(cur=cur, inbound_id=0, provider_id="wamid.late", kind="button", audience="staff",
                            sender=OWNER_PHONE, recipient_id=PLATFORM, hotel_slug="test-dubai", user_id=row.user_id)
    from clefs.core.hotels import get_hotel

    whatsapp.flow_list(ctx, get_hotel(cur, "test-dubai"), row)
    after = db.fetch_one("SELECT count(*) AS n FROM outbound_messages WHERE status = 'outside_window'")["n"]
    assert after == before + 1


# ─── Weekly report (DOCUMENT header) ───


def test_weekly_brief_carries_the_report_pdf_as_a_document_template(cur, hotel_uae, clock, wa_ready, owner_p, sig):
    from datetime import datetime, timezone

    clock.freeze(datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc))  # Monday 08:00 Dubai
    _only_owner(cur, hotel_uae, owner_p)
    sig("vis", section="money_available", cadence="weekly", aed=800, hours=24 * 5)
    out = brief_api.push_now(cur, hotel_uae.slug, cadence="weekly")
    assert out["sent_to"][0]["channel"] == "whatsapp"
    names = [p["template"]["name"] for p in sent_wa(wa_ready, "template")]
    assert names == ["owner_brief_v1", "weekly_report_v1"]
    rep = sent_wa(wa_ready, "template")[-1]["template"]["components"]
    header = next(c for c in rep if c["type"] == "header")["parameters"][0]
    assert header["type"] == "document" and header["document"]["filename"] == "clefs-weekly-2026-W40.pdf"
    assert "/files/" in header["document"]["link"] and "?t=" in header["document"]["link"]
    body = next(c for c in rep if c["type"] == "body")["parameters"]
    assert body[0]["text"] == "2026-W40"


def test_weekly_report_inside_the_window_is_a_plain_document(cur, hotel_uae, clock, wa_ready, owner_p, sig):
    from datetime import datetime, timezone

    clock.freeze(datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc))
    _only_owner(cur, hotel_uae, owner_p)
    tap("", kind="text", text="hello")  # the owner wrote first: the window is open
    brief_api.push_now(cur, hotel_uae.slug, cadence="weekly")
    docs = sent_wa(wa_ready, "document")
    assert docs and docs[-1]["document"]["filename"] == "clefs-weekly-2026-W40.pdf"


def test_member_who_left_gets_no_more_from_the_buttons(cur, pushed, wa_ready, owner_p):
    cur.execute("UPDATE hotel_memberships SET is_active = FALSE WHERE user_id = %s", (owner_p["id"],))
    tap(pushed["buttons"][1])
    assert last_text(wa_ready) == "You no longer have access to this hotel's brief."
    assert not sent_wa(wa_ready, "interactive")
