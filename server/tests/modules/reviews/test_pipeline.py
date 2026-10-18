"""Analysis, service links, drafts, the negative signal and reply actions."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clefs.core import signals
from clefs.core.hotels import get_hotel
from clefs.modules.reviews import pipeline, replies, store


def _review(cur, slug, *, rating=2, text="The AC was not working in room 504 for two nights.", ext="r1",
            platform="google", posted=None, lang=None, responded=False, source_id=None):
    row, _ = store.ingest(cur, slug, source_id, store.ReviewIn(
        platform=platform, external_id=ext, rating=rating, author_display="Omar Khan", text=text,
        posted_at=posted, language=lang, has_owner_response=responded))
    return row


def _request(cur, slug, *, room="504", category="Maintenance", at=None, rtype="ac"):
    from clefs.core import clock

    ms = int((at or clock.now()).timestamp() * 1000)
    cur.execute("INSERT INTO staff_requests (hotel_slug, room, type, category, timestamp) VALUES (%s, %s, %s, %s, %s) "
                "RETURNING id", (slug, room, rtype, category, ms))
    return cur.fetchone()["id"]


def ai_router(fake_ai, *, analysis=None, draft=None, times=10):
    """Queue a callable that answers by task."""

    def answer(req):
        if req.task == "reviews.analyze":
            return analysis
        if req.task == "reviews.draft_reply":
            return draft
        raise AssertionError(req.task)

    fake_ai.push(*([answer] * times))


def test_negative_review_without_ai_links_signals_and_drafts(cur, slug, now):
    hotel = get_hotel(cur, slug)
    posted = now.now() - timedelta(days=2)
    rid = _request(cur, slug, at=posted - timedelta(days=3))
    _request(cur, slug, room="601", at=posted - timedelta(days=3))  # another room: never linked
    _request(cur, slug, category="Housekeeping", at=posted - timedelta(days=3))  # another department
    review = _review(cur, slug, posted=posted)
    res = pipeline.run_pending(cur, hotel)
    assert res == {"analyzed": 1, "negative": 1, "failed": 0}
    cur.execute("SELECT * FROM rev_derived WHERE review_id = %s", (review["id"],))
    d = cur.fetchone()
    assert d["negative"] and d["model"] == "lexicon" and d["linked_request_ids"] == [rid]
    assert {t["key"] for t in d["themes"]} >= {"hvac"}
    sig = signals.list_signals(cur, slug, modules=["reviews"])
    assert len(sig) == 1 and sig[0]["kind"] == pipeline.SIGNAL_NEGATIVE and sig[0]["section"] == "reviews"
    reply = replies.live_reply(cur, slug, review["id"])
    assert reply["model"] == "template" and reply["checks"]["ok"] and reply["needs_approval"]
    assert any(e["kind"] == "quote" and e["value"] == reply["draft_text"] for e in sig[0]["evidence"])
    cur.execute("SELECT type, status, signal_id FROM actions WHERE id = %s", (reply["action_id"],))
    act = cur.fetchone()
    assert act["type"] == "reviews.reply_negative" and act["status"] == "proposed" and act["signal_id"] == sig[0]["id"]


def test_second_run_is_a_no_op(cur, slug, now):
    hotel = get_hotel(cur, slug)
    _review(cur, slug)
    pipeline.run_pending(cur, hotel)
    assert pipeline.run_pending(cur, hotel) == {"analyzed": 0, "negative": 0, "failed": 0}


def test_model_draft_breaking_policy_falls_back_to_template(cur, slug, now, fake_ai):
    hotel = get_hotel(cur, slug)
    review = _review(cur, slug)
    ai_router(fake_ai, analysis={
        "language": "en", "sentiment": -0.7, "summary_en": "AC broken two nights", "severity": "medium",
        "room_hint": "504", "stay_date_guess": None,
        "themes": [{"key": "hvac", "polarity": "negative", "summary_en": "AC broken"}],
    }, draft={"text": "Dear Omar \u2014 so sorry, it was our fault. We will refund your stay.", "language": "en"})
    pipeline.run_pending(cur, hotel)
    reply = replies.live_reply(cur, slug, review["id"])
    assert reply["model"] == "template" and "\u2014" not in reply["draft_text"]
    rejected = reply["checks"]["model_draft_rejected"]
    assert rejected["liability"] == ["our fault"] and rejected["compensation"] == ["refund"]


def test_arabic_review_gets_an_arabic_model_reply(cur, slug, now, fake_ai):
    hotel = get_hotel(cur, slug)
    review = _review(cur, slug, rating=3, text="الغرفة كانت غير نظيفة والمكيف لا يعمل", ext="ar1")
    assert review["language"] == "ar"
    ai_router(fake_ai, analysis={
        "language": "ar", "sentiment": -0.5, "summary_en": "Room dirty, AC not working", "severity": "medium",
        "room_hint": None, "stay_date_guess": None,
        "themes": [{"key": "cleanliness", "polarity": "negative", "summary_en": "Dirty room"}],
    }, draft={"text": "عزيزي عمر، شكرا لملاحظاتك. نأسف لتجربتك ونرجو التواصل معنا مباشرة. فريق الفندق",
              "language": "ar"})
    pipeline.run_pending(cur, hotel)
    reply = replies.live_reply(cur, slug, review["id"])
    assert reply["model"] == "llm" and reply["language"] == "ar" and reply["checks"]["ok"]
    assert replies.word_count(reply["draft_text"]) < 120


def test_positive_review_gets_a_suggested_positive_reply_and_no_signal(cur, slug, now):
    hotel = get_hotel(cur, slug)
    review = _review(cur, slug, rating=5, text="Lovely stay, friendly staff and a great pool.", ext="p1")
    pipeline.run_pending(cur, hotel)
    reply = replies.live_reply(cur, slug, review["id"])
    cur.execute("SELECT type, status, dial_level_at_creation FROM actions WHERE id = %s", (reply["action_id"],))
    act = cur.fetchone()
    assert act["type"] == "reviews.reply_positive" and act["status"] == "proposed"
    assert act["dial_level_at_creation"] == "suggest" and reply["needs_approval"]
    assert signals.list_signals(cur, slug, modules=["reviews"]) == []


@pytest.mark.parametrize("case", ["old", "answered"])
def test_old_or_answered_reviews_get_no_draft(cur, slug, now, case):
    hotel = get_hotel(cur, slug)
    kw = {"posted": now.now() - timedelta(days=90)} if case == "old" else {"responded": True}
    review = _review(cur, slug, **kw)
    pipeline.run_pending(cur, hotel)
    assert replies.live_reply(cur, slug, review["id"]) is None
    sig = signals.list_signals(cur, slug, modules=["reviews"])
    assert (len(sig) == 1) is (case == "answered")  # an answered complaint is still a complaint
