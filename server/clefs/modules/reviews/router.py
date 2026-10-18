"""
Reviews console routes. Every path here lives under /hotels/{slug}/reviews/
(Canon 13). Forwarded review emails arrive through the foundation's
/webhooks/email/resend (purpose 'reviews', handled in jobs.py).

Permissions (module.py): reviews.view reads; reviews.manage connects
sources, imports, edits, approves, posts and copies replies. Granting
auto-reply consent is owner-only. Every write is audited as
reviews.<entity>.<verb>. The HTTP contract is documented in README.md.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from clefs import db, files, jobs, ratelimit
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import actions
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.reviews import autonomy, pipeline, places, queries, replies, store, trends
from clefs.modules.reviews.settings import get_settings
from clefs.modules.reviews.sources import csv_upload

router = APIRouter(prefix="/hotels/{slug}/reviews", tags=["reviews"])

VIEW = require_perm("reviews.view")
MANAGE = require_perm("reviews.manage")


def _not_found() -> ClefsError:
    return ClefsError(404, "Review not found", "review_not_found")


# ─── Feed and detail ───


@router.get("")
def list_reviews(
    platform: str | None = None, negative: bool | None = None, unanswered: bool | None = None,
    theme: str | None = None, max_rating: float | None = Query(None, ge=0, le=5),
    days: int = Query(90, ge=1, le=730), limit: int = Query(50, ge=1, le=200), before_id: int | None = None,
    ctx: HotelCtx = Depends(VIEW),
) -> list[dict]:
    with db.db(readonly=True) as cur:
        return queries.feed(cur, ctx.slug, ctx, platform=platform, negative=negative, unanswered=unanswered,
                            theme=theme, max_rating=max_rating, days=days, limit=limit, before_id=before_id)


@router.get("/summary")
def get_summary(days: int = Query(30, ge=1, le=365), ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return queries.summary(cur, ctx.slug, days=days)


@router.get("/themes")
def get_themes(days: int = Query(30, ge=1, le=365), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return queries.themes(cur, ctx.slug, days=days)


@router.get("/trends")
def get_trends(weeks: int = Query(12, ge=2, le=52), ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return trends.weekly_series(cur, get_hotel(cur, ctx.slug), weeks=weeks)


@router.get("/{review_id:int}")
def get_review(review_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        out = queries.detail(cur, ctx.slug, review_id, ctx)
        if out is None:
            raise _not_found()
        return out


@router.post("/{review_id:int}/analyze")
def analyze_review(review_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Run the analysis again (and draft, signal and act as the job would)."""
    with db.db() as cur:
        review = store.get_review(cur, ctx.slug, review_id)
        if review is None:
            raise _not_found()
        res = pipeline.process_review(cur, get_hotel(cur, ctx.slug), review)
        audit(cur, ctx, "reviews.review.analyze", "rev_review", review_id, after=res)
        return res


class DraftIn(BaseModel):
    replace_edited: bool = False


@router.post("/{review_id:int}/draft")
def redraft(review_id: int, body: DraftIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """A fresh draft for this review (keeps a person's edit unless
    replace_edited); creates the reply action when there is none."""
    body = body or DraftIn()
    with db.db() as cur:
        review = store.get_review(cur, ctx.slug, review_id)
        if review is None:
            raise _not_found()
        hotel = get_hotel(cur, ctx.slug)
        cur.execute("SELECT * FROM rev_derived WHERE review_id = %s", (review_id,))
        derived = cur.fetchone()
        if derived is None:
            pipeline.process_review(cur, hotel, review)
            cur.execute("SELECT * FROM rev_derived WHERE review_id = %s", (review_id,))
            derived = cur.fetchone()
            review = store.get_review(cur, ctx.slug, review_id)
        reply = pipeline.ensure_draft(cur, hotel, review, dict(derived), get_settings(cur, ctx.slug),
                                      replace_edited=body.replace_edited)
        reply = pipeline.ensure_action(cur, hotel, review, dict(derived), reply)
        audit(cur, ctx, "reviews.reply.draft", "rev_reply", reply["id"], after={"model": reply["model"]})
        return reply


# ─── Replies ───


def _settle_action(cur: Any, reply: dict, decision: str, ctx: HotelCtx) -> None:
    """Keep the reply's action in step with a console decision: approving
    here approves the action (its executor then posts through GBP when it
    can, or finds the reply already posted or copied); discarding ignores
    it. An action already moved on (the dial, the brief) is left alone."""
    if not reply.get("action_id"):
        return
    cur.execute("SELECT status FROM actions WHERE id = %s", (reply["action_id"],))
    row = cur.fetchone()
    if not row or row["status"] not in ("proposed", "snoozed"):
        return
    cur.execute("SAVEPOINT rev_settle")
    try:
        actions.decide(cur, reply["action_id"], decision, actor=ctx, via="console")
        cur.execute("RELEASE SAVEPOINT rev_settle")
    except ClefsError:
        cur.execute("ROLLBACK TO SAVEPOINT rev_settle")


def _negative(cur: Any, reply: dict) -> bool:
    cur.execute("SELECT negative FROM rev_derived WHERE review_id = %s", (reply["review_id"],))
    row = cur.fetchone()
    return bool(row and row["negative"])


class ReplyEdit(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.get("/replies")
def list_replies(status: str = "draft,approved", limit: int = Query(50, ge=1, le=200),
                 ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    wanted = [s for s in status.split(",") if s in ("draft", "approved", "copied", "posted", "discarded")]
    with db.db(readonly=True) as cur:
        cur.execute(f"SELECT {replies.REPLY_COLS} FROM rev_replies WHERE hotel_slug = %s AND status = ANY(%s) "
                    "ORDER BY created_at DESC LIMIT %s", (ctx.slug, wanted or ["draft"], limit))
        return [dict(r) for r in cur.fetchall()]


@router.patch("/replies/{reply_id}")
def edit_reply(reply_id: int, body: ReplyEdit, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before = replies.get_reply(cur, ctx.slug, reply_id)
        style = get_settings(cur, ctx.slug).reply_style
        row = replies.edit(cur, ctx.slug, reply_id, body.text, negative=_negative(cur, before), contact=style.contact)
        audit(cur, ctx, "reviews.reply.edit", "rev_reply", reply_id, before={"text": before["draft_text"]},
              after={"text": row["draft_text"], "checks": row["checks"]})
        return row


@router.post("/replies/{reply_id}/approve")
def approve_reply(reply_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Approve and send: posts through GBP when this review allows it (the
    action's executor), else the reply waits approved for Copy."""
    with db.db() as cur:
        row = replies.approve(cur, ctx.slug, reply_id, user_id=ctx.principal.user_id)
        audit(cur, ctx, "reviews.reply.approve", "rev_reply", reply_id, after={"status": row["status"]})
        _settle_action(cur, row, "approve", ctx)
        return replies.get_reply(cur, ctx.slug, reply_id)


@router.post("/replies/{reply_id}/post")
def post_reply(reply_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Post to Google now (explicit per-reply click). 409 not_postable when
    the review is not a GBP-synced Google review or Google is disconnected."""
    with db.db() as cur:
        row = replies.post(cur, ctx.slug, reply_id, user_id=ctx.principal.user_id, via="gbp")
        audit(cur, ctx, "reviews.reply.post", "rev_reply", reply_id,
              after={"status": row["status"], "platform_reply_id": row["platform_reply_id"]})
        _settle_action(cur, row, "approve", ctx)
        return row


@router.post("/replies/{reply_id}/copied")
def reply_copied(reply_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """The Copy button: the text was copied to post by hand."""
    with db.db() as cur:
        row = replies.mark_copied(cur, ctx.slug, reply_id, user_id=ctx.principal.user_id)
        audit(cur, ctx, "reviews.reply.copy", "rev_reply", reply_id, after={"status": row["status"]})
        _settle_action(cur, row, "approve", ctx)
        return row


@router.post("/replies/{reply_id}/discard")
def discard_reply(reply_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = replies.discard(cur, ctx.slug, reply_id)
        audit(cur, ctx, "reviews.reply.discard", "rev_reply", reply_id, after={"status": row["status"]})
        _settle_action(cur, row, "ignore", ctx)
        return row


# ─── Sources ───

SourceKind = Literal["serpapi_google", "gbp", "tripadvisor"]


class SourceIn(BaseModel):
    kind: SourceKind
    label: str | None = Field(None, max_length=80)
    # serpapi_google: {query?, location?}; gbp: {location_name?}; tripadvisor: {location_id}
    config: dict = Field(default_factory=dict)


class SourcePatch(BaseModel):
    label: str | None = Field(None, max_length=80)
    config: dict | None = None
    status: Literal["active", "paused"] | None = None


_CONFIG_KEYS = {"serpapi_google": {"query", "location"}, "gbp": {"location_name", "account"},
                "tripadvisor": {"location_id", "language"}, "csv": set(), "email": set()}


def _clean_config(kind: str, config: dict, existing: dict | None = None) -> dict:
    """Only the keys a person may set; resolved ids (data_id, place_id) are
    the sync's own and survive an edit."""
    bad = set(config) - _CONFIG_KEYS[kind]
    if bad:
        raise ClefsError(400, f"Unknown config keys: {', '.join(sorted(bad))}", "invalid_config")
    out = dict(existing or {})
    for k, v in config.items():
        out[k] = str(v)[:200] if v is not None else None
    if kind == "tripadvisor" and out.get("location_id") and not str(out["location_id"]).isdigit():
        raise ClefsError(400, "Tripadvisor location_id is a number", "invalid_config")
    if kind == "serpapi_google" and ({"query", "location"} & set(config)):
        for k in ("data_id", "place_id", "title", "address"):
            out.pop(k, None)  # a new search target: resolve the place again
    return out


def _source_out(cur: Any, slug: str, s: dict) -> dict:
    s = dict(s)
    if s["kind"] == "gbp":
        from clefs.core import oauth

        conn = oauth.get_connection(cur, slug, "google_business")
        s["google"] = {"status": (conn or {}).get("status", "not_connected"),
                       "account_ref": (conn or {}).get("account_ref")}
    return s


@router.get("/sources")
def list_sources(ctx: HotelCtx = Depends(VIEW)) -> dict:
    from clefs.notify import hotel_inbound_address

    with db.db(readonly=True) as cur:
        cfg = get_settings(cur, ctx.slug)
        return {
            "sources": [_source_out(cur, ctx.slug, s) for s in store.list_sources(cur, ctx.slug)],
            "inbound_address": hotel_inbound_address(cur, ctx.slug, "reviews"),
            "serpapi": {"enabled": cfg.serpapi_enabled,
                        "disclosure": "Google Maps reviews are fetched through SerpApi, a third-party service. "
                                      "Connect Google Business Profile to use Google's own API instead."},
            "auto_reply_consent": autonomy.has_consent(cur, ctx.slug),
        }


@router.post("/sources", status_code=201)
def create_source(body: SourceIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = store.create_source(cur, ctx.slug, body.kind, config=_clean_config(body.kind, body.config),
                                  label=body.label, created_by=ctx.principal.user_id)
        audit(cur, ctx, "reviews.source.create", "rev_source", row["id"], after={"kind": row["kind"],
                                                                                 "config": row["config"]})
        return _source_out(cur, ctx.slug, row)


@router.patch("/sources/{source_id}")
def update_source(source_id: int, body: SourcePatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before = store.get_source(cur, ctx.slug, source_id)
        changes: dict[str, Any] = {}
        if body.label is not None:
            changes["label"] = body.label
        if body.config is not None:
            changes["config"] = _clean_config(before["kind"], body.config, before["config"])
        if body.status is not None:
            changes["status"] = body.status
        row = store.update_source(cur, ctx.slug, source_id, **changes)
        audit(cur, ctx, "reviews.source.update", "rev_source", source_id,
              before={k: before.get(k) for k in changes}, after={k: row.get(k) for k in changes})
        return _source_out(cur, ctx.slug, row)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    """Removes the connection; its reviews stay (source_id becomes NULL)."""
    with db.db() as cur:
        before = store.get_source(cur, ctx.slug, source_id)
        cur.execute("DELETE FROM rev_sources WHERE hotel_slug = %s AND id = %s", (ctx.slug, source_id))
        audit(cur, ctx, "reviews.source.delete", "rev_source", source_id, before={"kind": before["kind"]})
    return Response(status_code=204)


class SyncIn(BaseModel):
    source_id: int | None = None


@router.post("/sync", status_code=202)
def sync_now(body: SyncIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Queue a sync of one source (or all) plus analysis of what it finds.
    6 per hour per hotel: SerpApi pages are billed."""
    body = body or SyncIn()
    with db.db() as cur:
        if body.source_id is not None:
            store.get_source(cur, ctx.slug, body.source_id)
        if not ratelimit.hit(cur, f"reviews:sync:{ctx.slug}", limit=6, window_s=3600):
            raise ClefsError(429, "Sync was run recently; try again later", "too_many_requests")
        job_id = jobs.enqueue("reviews.sync_now", {"source_id": body.source_id}, hotel_slug=ctx.slug, cur=cur,
                              dedupe_key=f"reviews.sync_now:{ctx.slug}:{body.source_id or 'all'}")
        audit(cur, ctx, "reviews.source.sync", "rev_source", body.source_id or "all", after={"job_id": job_id})
        return {"queued": job_id is not None, "job_id": job_id}


class ConsentIn(BaseModel):
    granted: bool


@router.put("/sources/{source_id}/auto-reply-consent")
def auto_reply_consent(source_id: int, body: ConsentIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Owner-only: the GBP API policy needs the owner's prior specific and
    express consent to unattended replies. Withdrawing is open to managers
    (making the dial safer never needs more than manage)."""
    if body.granted and ctx.role != "owner" and not ctx.principal.is_clefs_admin:
        raise ClefsError(403, "Only the owner can allow automatic replies", "owner_only")
    with db.db() as cur:
        row = autonomy.set_auto_reply_consent(cur, ctx.slug, source_id, body.granted, ctx=ctx)
        return _source_out(cur, ctx.slug, row)


# ─── CSV import ───


class ImportIn(BaseModel):
    preset: Literal["booking", "agoda", "expedia", "generic"] = "booking"
    csv: str | None = Field(None, max_length=5_000_000)
    file_id: str | None = Field(None, max_length=64)
    scale: Literal[5, 10] | None = None
    dry_run: bool = False


@router.post("/import")
def import_reviews(body: ImportIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Booking.com / Agoda / Expedia extranet exports (or a generic layout),
    pasted or uploaded first through /hotels/{slug}/files."""
    with db.db() as cur:
        if body.csv:
            text = body.csv
        elif body.file_id is not None:
            _, data = files.get_file(cur, ctx.slug, body.file_id)
            text = data.decode("utf-8-sig", errors="replace")
        else:
            raise ClefsError(400, "Send csv text or a file_id", "csv_missing")
        res = csv_upload.import_csv(cur, get_hotel(cur, ctx.slug), text, preset=body.preset, scale=body.scale,
                                    dry_run=body.dry_run)
        if not body.dry_run:
            audit(cur, ctx, "reviews.import.csv", "rev_source", res["source_id"],
                  after={k: res[k] for k in ("preset", "rows", "created", "updated", "skipped")})
        return res


# ─── Competitors (live Places, never stored) ───


class CompetitorsIn(BaseModel):
    place_ids: list[str] = Field(default_factory=list, max_length=places.MAX_PLACES)
    own_place_id: str | None = None


@router.get("/competitors")
def get_competitors(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:  # not read-only: the rate limiter writes
        if not ratelimit.hit(cur, f"reviews:places:{ctx.slug}", limit=60, window_s=600):
            raise ClefsError(429, "Too many refreshes; try again in a few minutes", "too_many_requests")
        return {**places.get_set(cur, ctx.slug), **places.live(cur, ctx.slug)}


@router.put("/competitors")
def put_competitors(body: CompetitorsIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before = places.get_set(cur, ctx.slug)
        row = places.put_set(cur, ctx.slug, body.place_ids, body.own_place_id, user_id=ctx.principal.user_id)
        audit(cur, ctx, "reviews.competitors.update", "rev_competitors", ctx.slug,
              before={"place_ids": before["place_ids"]}, after={"place_ids": row["place_ids"]})
        return row
