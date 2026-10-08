"""Graph actions from one event (staffing gap, pre-arrival offer, KB
suggestion), campaign drafts with the 09:00-18:00 send window, and the
Google Hotel Center checklist (02 spec 4.3)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.core import actions as core_actions
from clefs.core import signals as core_signals
from clefs.modules.demand import campaigns, events, ghc, graph_actions as ga

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
DUBAI = ZoneInfo("Asia/Dubai")
START = date(2026, 10, 13)


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    from clefs.modules.demand import curated

    clock.freeze(NOW)
    curated.sync(cur, force=True)
    return hotel_uae


@pytest.fixture
def show(cur, hotel):
    eid = events.ingest(cur, {"title": "Zephyr Widget Summit", "category": "CONFERENCE", "start": START,
                              "end": START + timedelta(days=2), "venue_codes": ["DWTC"], "attendance": 60000},
                        source="CURATED", external_id="zw:g")["event_id"]
    return cur.execute("SELECT * FROM dr_events WHERE id = %s", (eid,)) or dict(cur.fetchone())


def _res(cur, slug, n, check_in, check_out, rooms=1):
    for i in range(n):
        cur.execute("INSERT INTO reservations (hotel_slug, pms_res_id, check_in, check_out, nights, rooms_count, status, "
                    "currency) VALUES (%s, %s, %s, %s, %s, %s, 'CONFIRMED', 'AED')",
                    (slug, f"r-{check_in}-{check_out}-{i}", check_in, check_out, (check_out - check_in).days, rooms))


def test_send_window_clamp():
    assert campaigns.clamp_send_window(datetime(2026, 10, 1, 20, 0, tzinfo=DUBAI)) == \
        datetime(2026, 10, 2, 9, 0, tzinfo=DUBAI)
    assert campaigns.clamp_send_window(datetime(2026, 10, 1, 7, 30, tzinfo=DUBAI)) == \
        datetime(2026, 10, 1, 9, 0, tzinfo=DUBAI)
    noon = datetime(2026, 10, 1, 12, 0, tzinfo=DUBAI)
    assert campaigns.clamp_send_window(noon) == noon
    assert campaigns.clamp_send_window(datetime(2026, 10, 1, 18, 0, tzinfo=DUBAI)).hour == 9
    # 14 days before the event at 10:00, never in the past
    assert campaigns.suggested_send_at(date(2026, 12, 7), now=NOW) == datetime(2026, 11, 23, 10, 0, tzinfo=DUBAI)
    late = datetime(2026, 12, 1, 19, 0, tzinfo=DUBAI)
    assert campaigns.suggested_send_at(date(2026, 12, 7), now=late) == datetime(2026, 12, 2, 9, 0, tzinfo=DUBAI)


def test_staffing_formula_and_gap_signal(cur, hotel, show, monkeypatch):
    day = START + timedelta(days=1)
    _res(cur, hotel.slug, 20, START - timedelta(days=1), day)       # 20 departures
    _res(cur, hotel.slug, 15, START - timedelta(days=1), day + timedelta(days=2))  # 15 stayovers
    load = ga.housekeeping_load(cur, hotel.slug, day, stayover_ratio=0.8)
    assert load == {"departures": 20, "stayovers": 15, "rooms_to_clean": 32.0}
    assert ga.attendants_needed(32.0, 14) == 3
    # No roster on file: guidance kept, no signal.
    out = ga.staffing_guidance(cur, hotel, day, event=show)
    assert out["payload"]["roster_unknown"] and out["payload"]["gap"] is None
    monkeypatch.setattr(ga, "rostered_attendants", lambda cur, hotel, d: 1)
    out = ga.staffing_guidance(cur, hotel, day, event=show)
    assert out["payload"]["gap"] == 2 and out["action_id"] is None  # the row existed; action attached below
    [row] = ga.list_guidance(cur, hotel.slug, kind="STAFFING")
    assert row["action_id"]
    sigs = [s for s in core_signals.list_signals(cur, hotel.slug, modules=["demand"]) if s["kind"] == "dr.staffing.gap"]
    assert len(sigs) == 1 and sigs[0]["section"] == "service_risk"


def _owner_ctx(cur, make_user, slug):
    from clefs.auth.deps import Principal, resolve_ctx

    user = make_user("owner", slug)
    return resolve_ctx(Principal(kind="user", user_id=user["id"], label="Owner", session_kind="web"), slug, cur=cur)


def test_pre_arrival_offer_and_kb_suggestion(app, cur, hotel, show, monkeypatch, run_jobs, make_user):
    _res(cur, hotel.slug, 3, START - timedelta(days=1), START + timedelta(days=2))
    out = ga.for_event(cur, hotel, show)
    [offer] = ga.list_guidance(cur, hotel.slug, kind="PRE_ARRIVAL_OFFER")
    assert offer["payload"]["booked_arrivals"] == 3 and offer["action_id"]
    [kb] = ga.list_guidance(cur, hotel.slug, kind="KB_UPDATE")
    assert "World Trade Centre station" in kb["payload"]["answer_en"] and out["kb"] == kb["id"]
    # Approval runs the executor: concierge.api.propose_kb_entry queues the entry.
    from clefs.modules.concierge import api as concierge_api

    calls = []
    monkeypatch.setattr(concierge_api, "propose_kb_entry", lambda cur, slug, **kw: calls.append(kw) or 77)
    core_actions.decide(cur, kb["action_id"], "approve", actor=_owner_ctx(cur, make_user, hotel.slug),
                        hotel_slug=hotel.slug)
    run_jobs(only=["core.actions.execute"])
    act = core_actions.get_action(cur, hotel.slug, kb["action_id"])
    assert act["status"] == "done" and calls and calls[0]["source"] == "demand"
    # The event moves: its guidance and actions are referenced.
    refs = events.references(cur, str(show["id"]))
    assert any(r["ref_type"] == "action" for r in refs)


def test_campaign_drafts_are_export_only(cur, hotel, show, monkeypatch):
    from clefs.modules.guests import api as guests_api

    monkeypatch.setattr(guests_api, "marketing_audience", lambda cur, slug, **kw: [
        {"contact_ref": "ab" * 16, "channel": kw["channel"], "locale": "en"},
        {"contact_ref": "cd" * 16, "channel": kw["channel"], "locale": "ar"}])
    monkeypatch.setattr(guests_api, "unsubscribe_path", lambda slug, **kw: "/public/guests/unsubscribe/t")
    drafts = campaigns.create_drafts(cur, hotel, event_id=str(show["id"]), use_llm=False,
                                     send_at=datetime(2026, 10, 1, 21, 0, tzinfo=DUBAI))
    assert [d["channel"] for d in drafts] == ["WHATSAPP", "INSTAGRAM", "EMAIL"]
    assert all(d["suggested_send_at"] == "2026-10-02T09:00:00+04:00" and d["export_only"] for d in drafts)
    assert drafts[0]["audience"]["count"] == 2 and drafts[1]["audience"]["kind"] == "organic_post"
    assert "contacts" not in drafts[0]["audience"]
    name, text = campaigns.export_csv(cur, hotel.slug, drafts[2]["id"])
    import csv
    import io

    lines = list(csv.reader(io.StringIO(text)))
    assert name.endswith("email.csv") and len(lines) == 3 and lines[1][-1] == "/public/guests/unsubscribe/t"
    assert lines[1][0] == "ab" * 16 and "@" not in text  # refs only, never an address
    assert campaigns.list_drafts(cur, hotel.slug)[0]["status"] in ("DRAFT", "EXPORTED")
    with pytest.raises(Exception) as e:
        campaigns.update_draft(cur, hotel.slug, drafts[0]["id"], copy={"text": "Rates will increase, guaranteed"})
    assert e.value.code == "copy_lint"


def test_campaign_export_rechecks_consent(cur, hotel, show, monkeypatch):
    """Regression (security-privacy-3): the audience frozen at draft time is
    re-checked against Guests at export, so a guest who unsubscribed or was
    suppressed since is not on the send list; the drop is recorded."""
    import csv
    import io

    from clefs.modules.guests import api as guests_api

    live = {"ab" * 16, "cd" * 16}
    seen: list[dict] = []

    def audience(cur, slug, **kw):
        seen.append(kw)
        return [{"contact_ref": h, "channel": kw["channel"], "locale": "en"} for h in sorted(live)]

    monkeypatch.setattr(guests_api, "marketing_audience", audience)
    monkeypatch.setattr(guests_api, "unsubscribe_path", lambda slug, **kw: "/public/guests/unsubscribe/t")
    [wa, em] = campaigns.create_drafts(cur, hotel, event_id=str(show["id"]), channels=["WHATSAPP", "EMAIL"],
                                       use_llm=False)
    assert wa["audience"]["count"] == 2
    live.discard("cd" * 16)  # the guest unsubscribes after the draft was made
    live.add("ef" * 16)      # a new consenting guest is not added to an approved list
    _, text = campaigns.export_csv(cur, hotel.slug, em["id"])
    refs = [row[0] for row in list(csv.reader(io.StringIO(text)))[1:]]
    assert refs == ["ab" * 16]
    assert seen[-1]["channel"] == "email" and seen[-1]["segment"]["countries"] == wa["audience"]["countries"]
    chk = campaigns.get_draft(cur, hotel.slug, em["id"])["audience"]["export_check"]
    assert chk["exported_count"] == 1 and chk["dropped_count"] == 1 and chk["recheck"] == "ok"
    # Guests unavailable at export: fail closed, no contacts, the drop is recorded.
    monkeypatch.setattr(guests_api, "marketing_audience", lambda cur, slug, **kw: 1 / 0)
    _, text = campaigns.export_csv(cur, hotel.slug, wa["id"])
    rows = list(csv.reader(io.StringIO(text)))[1:]
    assert len(rows) == 1 and rows[0][0] == ""
    chk = campaigns.get_draft(cur, hotel.slug, wa["id"])["audience"]["export_check"]
    assert chk == {**chk, "exported_count": 0, "dropped_count": 2, "recheck": "guests_unavailable"}


def test_ghc_checklist_and_deadline_signal(cur, hotel):
    cl = ghc.checklist(cur, hotel)
    assert cl["deadline"] == "2026-09-30" and cl["open_items"] == 5 and cl["days_left"] == 1
    assert "google_third_party_rates_unavailable_2026_09_30" in cl["claim_ids"]
    assert "not affected" in cl["summary"]
    ghc.check_deadline(cur, hotel)
    [s] = [s for s in core_signals.list_signals(cur, hotel.slug, modules=["demand"]) if s["kind"] == "dr.ghc.deadline"]
    assert s["section"] == "money_available" and "5 checklist item(s) open" in s["title"]
    ghc.update(cur, hotel, [{"item_code": c, "status": "DONE"} for c in ghc.CODES[:4]]
               + [{"item_code": ghc.CODES[4], "status": "NA"}], user_id=None)
    assert not [s for s in core_signals.list_signals(cur, hotel.slug, modules=["demand"])
                if s["kind"] == "dr.ghc.deadline"]
    with pytest.raises(Exception) as e:
        ghc.update(cur, hotel, [{"item_code": "NOPE", "status": "DONE"}], user_id=None)
    assert e.value.code == "invalid_item"
