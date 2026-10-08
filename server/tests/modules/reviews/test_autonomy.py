"""04 spec 1.4: reviews.reply_positive / reply_negative on the autonomy dial,
the auto-reply consent gate and the executor's GBP / Copy paths."""

from __future__ import annotations

import json

import pytest

from clefs.core import actions
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.reviews import autonomy, pipeline, replies, store
from tests.modules.reviews.conftest import GBP_LOCATION


def _gbp_review(cur, slug, src, *, rating=5, text="Lovely stay, friendly staff and a great pool.", ext="g1"):
    row, _ = store.ingest(cur, slug, src["id"], store.ReviewIn(
        platform="google", external_id=ext, rating=rating, author_display="Anna Smith", text=text,
        platform_ref=f"{GBP_LOCATION}/reviews/{ext}"))
    return row


def _action(cur, reply):
    cur.execute("SELECT * FROM actions WHERE id = %s", (reply["action_id"],))
    return cur.fetchone()


def test_auto_needs_recorded_consent(cur, slug, gbp_source, owner_ctx):
    with pytest.raises(ClefsError) as exc:
        actions.set_autonomy(cur, slug, autonomy.POSITIVE, level="auto", actor=actions.SYSTEM)
    assert exc.value.status == 409 and exc.value.code == "auto_reply_consent_required"
    # approve is always allowed; the negative type can never go above approve.
    actions.set_autonomy(cur, slug, autonomy.POSITIVE, level="approve", actor=actions.SYSTEM)
    with pytest.raises(ClefsError):
        actions.set_autonomy(cur, slug, autonomy.NEGATIVE, level="auto", actor=actions.SYSTEM)
    autonomy.set_auto_reply_consent(cur, slug, gbp_source["id"], True, ctx=owner_ctx)
    src = store.get_source(cur, slug, gbp_source["id"])
    assert src["auto_reply_consent"] and src["auto_reply_consent_by"] and src["auto_reply_consent_at"]
    actions.set_autonomy(cur, slug, autonomy.POSITIVE, level="auto", actor=actions.SYSTEM)


def test_consent_only_on_gbp_sources(cur, slug, owner_ctx):
    src = store.create_source(cur, slug, "tripadvisor", config={"location_id": "123"})
    with pytest.raises(ClefsError) as exc:
        autonomy.set_auto_reply_consent(cur, slug, src["id"], True, ctx=owner_ctx)
    assert exc.value.code == "consent_needs_gbp"


def test_dial_posts_a_clean_five_star_reply_through_gbp(cur, slug, now, gbp_source, owner_ctx, http_fake,
                                                        run_jobs):
    autonomy.set_auto_reply_consent(cur, slug, gbp_source["id"], True, ctx=owner_ctx)
    actions.set_autonomy(cur, slug, autonomy.POSITIVE, level="auto", actor=actions.SYSTEM)
    review = _gbp_review(cur, slug, gbp_source)
    pipeline.run_pending(cur, get_hotel(cur, slug))
    reply = replies.live_reply(cur, slug, review["id"])
    assert not reply["needs_approval"] and _action(cur, reply)["status"] == "approved"
    run_jobs(only=["core.actions.execute"])
    reply = replies.live_reply(cur, slug, review["id"])
    assert reply["status"] == "posted" and reply["posted_via"] == "dial"
    put = [r for r in http_fake.requests if r.method == "PUT"]
    assert len(put) == 1 and str(put[0].url).endswith(f"{GBP_LOCATION}/reviews/g1/reply")
    assert json.loads(put[0].content)["comment"] == reply["draft_text"]
    assert put[0].headers["authorization"] == "Bearer tok"
    assert store.get_review(cur, slug, review["id"])["has_owner_response"]
    assert _action(cur, reply)["status"] == "done"


@pytest.mark.parametrize("rating,text", [
    (3, "Average stay, nothing special."),
    (5, "Friendly staff. The bathroom was dirty and the shower was broken."),
])
def test_three_stars_or_a_negative_theme_is_capped_even_with_consent(cur, slug, now, gbp_source, owner_ctx,
                                                                     http_fake, rating, text):
    autonomy.set_auto_reply_consent(cur, slug, gbp_source["id"], True, ctx=owner_ctx)
    actions.set_autonomy(cur, slug, autonomy.POSITIVE, level="auto", actor=actions.SYSTEM)
    review = _gbp_review(cur, slug, gbp_source, rating=rating, text=text)
    pipeline.run_pending(cur, get_hotel(cur, slug))
    reply = replies.live_reply(cur, slug, review["id"])
    act = _action(cur, reply)
    assert act["status"] == "proposed" and reply["needs_approval"]
    assert not [r for r in http_fake.requests if r.method == "PUT"]


def test_human_approval_of_a_non_gbp_review_leaves_it_ready_to_copy(cur, slug, now, owner_ctx, run_jobs):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="booking", external_id="b1", rating=4,
                                                          rating_scale=10, text="Dirty room, rude staff."))
    pipeline.run_pending(cur, get_hotel(cur, slug))
    reply = replies.live_reply(cur, slug, row["id"])
    act = _action(cur, reply)
    assert act["type"] == autonomy.NEGATIVE
    actions.decide(cur, act["id"], "approve", actor=owner_ctx)
    run_jobs(only=["core.actions.execute"])
    reply = replies.live_reply(cur, slug, row["id"])
    assert reply["status"] == "approved" and reply["posted_at"] is None


def test_withdrawing_consent_lowers_the_dial(cur, slug, gbp_source, owner_ctx):
    autonomy.set_auto_reply_consent(cur, slug, gbp_source["id"], True, ctx=owner_ctx)
    actions.set_autonomy(cur, slug, autonomy.POSITIVE, level="auto", actor=actions.SYSTEM)
    autonomy.set_auto_reply_consent(cur, slug, gbp_source["id"], False, ctx=owner_ctx)
    assert actions.resolve_level(cur, slug, autonomy.POSITIVE).configured == "approve"


def test_a_policy_breaking_edit_fails_the_action_instead_of_retrying(cur, slug, now, owner_ctx, run_jobs):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="e1", rating=2,
                                                          text="Slow check-in."))
    pipeline.run_pending(cur, get_hotel(cur, slug))
    reply = replies.live_reply(cur, slug, row["id"])
    replies.edit(cur, slug, reply["id"], "We will refund you. Contact us.", negative=True, contact=None)
    actions.decide(cur, reply["action_id"], "approve", actor=owner_ctx)
    run_jobs(only=["core.actions.execute"])
    act = _action(cur, reply)
    assert act["status"] == "failed"
    assert replies.live_reply(cur, slug, row["id"])["status"] == "draft"


def test_transient_google_error_retries(cur, slug, now, gbp_source, owner_ctx, http_fake, run_jobs):
    http_fake.answers.clear()
    http_fake.on("PUT", "/reply", (503, {"error": "busy"}))
    review = _gbp_review(cur, slug, gbp_source, rating=5)
    pipeline.run_pending(cur, get_hotel(cur, slug))
    reply = replies.live_reply(cur, slug, review["id"])
    actions.decide(cur, reply["action_id"], "approve", actor=owner_ctx)
    run_jobs(only=["core.actions.execute"])
    assert _action(cur, reply)["status"] == "approved"  # queued again with backoff, not failed
    assert replies.live_reply(cur, slug, review["id"])["status"] == "draft"
