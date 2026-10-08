"""
Journeys console routes (04 spec 3.10): /hotels/{slug}/guests/journeys/...

Read routes need guests.journeys.view (owner, gm, front_desk); changes need
guests.journeys.manage (owner, gm). Template approval is the owner's gate
on every version (3.6); offers are the owner-approved "campaign" behind J3
and J8. No route here sends anything: the dispatcher and the dial do.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import actions, clock, money
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_module_settings, set_module_settings
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import steps, templates

router = APIRouter(prefix="/hotels/{slug}/guests/journeys", tags=["guests-journeys"])

VIEW = require_perm("guests.journeys.view")
MANAGE = require_perm("guests.journeys.manage")


# ─── Overview ───


@router.get("/overview")
def overview(slug: str, days: int = Query(30, ge=1, le=365), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Step funnel, capture rate, opt-outs, cost and the dial per step."""
    with db.db(readonly=True) as cur:
        since = clock.now() - timedelta(days=days)
        cur.execute(
            """
            SELECT step, status, skip_reason, COUNT(*) AS n FROM gst_journey_plans
            WHERE hotel_slug = %s AND created_at >= %s GROUP BY step, status, skip_reason
            """,
            (slug, since),
        )
        funnel: dict[str, dict[str, Any]] = {s: {"planned": 0, "sent": 0, "delivered": 0, "replied": 0, "observed": 0,
                                                 "proposed": 0, "skipped": {}, "other": 0} for s in steps.STEPS}
        for r in cur.fetchall():
            f = funnel[r["step"]]
            st, n = r["status"], int(r["n"])
            if st == "skipped":
                f["skipped"][r["skip_reason"] or "unknown"] = f["skipped"].get(r["skip_reason"] or "unknown", 0) + n
            elif st in ("planned", "deferred", "claimed"):
                f["planned"] += n
            elif st in ("sent", "delivered", "read", "replied"):
                f["sent"] += n
                f["delivered"] += n if st in ("delivered", "read", "replied") else 0
                f["replied"] += n if st == "replied" else 0
            elif st in f:
                f[st] += n
            else:
                f["other"] += n
        cur.execute(
            """
            SELECT COUNT(DISTINCT s.reservation_id) FILTER (WHERE UPPER(COALESCE(s.source, '')) IN ('BOOKING', 'AGODA', 'EXPEDIA'))
                       AS ota_stays,
                   COUNT(DISTINCT s.reservation_id) FILTER (WHERE UPPER(COALESCE(s.source, '')) IN ('BOOKING', 'AGODA', 'EXPEDIA')
                       AND EXISTS (SELECT 1 FROM gst_contact_points p WHERE p.hotel_slug = s.hotel_slug
                                   AND p.reservation_id = s.reservation_id AND p.provenance = 'guest_direct')) AS captured
            FROM gst_stays s WHERE s.hotel_slug = %s AND s.check_in >= %s
            """,
            (slug, (clock.now() - timedelta(days=days)).date()),
        )
        cap = dict(cur.fetchone())
        cur.execute("SELECT step, COUNT(*) AS n FROM gst_journey_optouts WHERE hotel_slug = %s AND opted_out_at >= %s "
                    "GROUP BY step", (slug, since))
        optouts = {r["step"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute("SELECT COALESCE(SUM(cost_micros), 0) AS m FROM gst_message_costs WHERE hotel_slug = %s AND charged_at >= %s",
                    (slug, since))
        micros = int(cur.fetchone()["m"])
        dial = {}
        for step, key in steps.ACTION_TYPES.items():
            a = actions.get_autonomy(cur, slug, key)
            dial[step] = {"action_type": key, "level": a.get("level"), "effective_level": a.get("effective_level"),
                          "max_level": a.get("max_level")}
        cur.execute(
            "SELECT COUNT(*) AS n FROM gst_journey_templates WHERE hotel_slug = %s AND active AND approved_at IS NOT NULL "
            "AND (lint->>'ok')::boolean IS TRUE AND (channel = 'email' OR meta_status = 'APPROVED')",
            (slug,),
        )
        approved = int(cur.fetchone()["n"])
        ccy = ctx.hotel.currency
        return {
            "days": days, "funnel": funnel, "dial": dial,
            "capture": {"ota_stays": cap["ota_stays"] or 0, "captured": cap["captured"] or 0,
                        "rate": round(100.0 * (cap["captured"] or 0) / cap["ota_stays"], 1) if cap["ota_stays"] else None},
            "optouts": optouts,
            "cost": {"currency": ccy, "amount": money.money_str(int(round(micros * 10 ** money.minor_units(ccy) / 1_000_000)), ccy)},
            "templates_approved": approved,
            "all_observing": approved == 0,
        }


# ─── Templates ───


def _template_out(t: dict) -> dict:
    return {
        "id": t["id"], "step": t["step"], "channel": t["channel"], "language": t["language"],
        "meta_name": t["meta_name"], "version": t["version"], "subject": t["subject"], "body": t["body"],
        "buttons": t["buttons"], "variables": t["variables"], "lint": t["lint"],
        "category_expected": t["category_expected"], "category_meta": t["category_meta"], "meta_status": t["meta_status"],
        "quality": t["quality"], "approved_at": t["approved_at"], "submitted_at": t["submitted_at"],
        "sendable": templates.is_sendable(t), "dir": "rtl" if t["language"] == "ar" else "ltr",
        "category_mismatch": bool(t["category_meta"]) and t["category_meta"] != t["category_expected"],
    }


@router.get("/templates")
def list_templates(slug: str, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        return [_template_out(t) for t in templates.list_templates(cur, slug)]


@router.post("/templates/{template_id}/approve")
def approve_template(slug: str, template_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = templates.approve(cur, slug, template_id, actor_user_id=ctx.principal.user_id)
        audit(cur, ctx, "guests.journeys.template_approve", "gst_journey_template", template_id,
              after={"step": row["step"], "channel": row["channel"], "language": row["language"], "version": row["version"]})
        return {**_template_out(row), "submission": row.get("submission")}


@router.post("/templates/{template_id}/revoke")
def revoke_template(slug: str, template_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = templates.revoke(cur, slug, template_id)
        if row is None:
            raise ClefsError(404, "Template not found", "template_not_found")
        audit(cur, ctx, "guests.journeys.template_revoke", "gst_journey_template", template_id)
        return _template_out(row)


# ─── Offers (J3, J8) ───

OFFER_COLS = ("id, hotel_slug, step, title_en, title_ar, price_minor, discount_pct, currency, code_prefix, valid_from, "
              "valid_to, approved_by, approved_at, active, created_at")


class OfferIn(BaseModel):
    step: Literal["j3", "j8"]
    title_en: str = Field(min_length=2, max_length=80)
    title_ar: str | None = Field(default=None, max_length=80)
    price: Decimal | None = Field(default=None, ge=0)
    discount_pct: Decimal | None = Field(default=None, gt=0, le=100)
    code_prefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,8}$")
    valid_from: date
    valid_to: date

    @model_validator(mode="after")
    def _check(self) -> "OfferIn":
        from clefs.modules.guests.journeys.templates import EM_DASH, REVIEW_WORDS, _has_word

        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must be on or after valid_from")
        for t in (self.title_en, self.title_ar or ""):
            if EM_DASH in t or _has_word(t, REVIEW_WORDS):
                raise ValueError("offer titles may not contain an em dash or ask for reviews")
        return self


def _offer_out(r: dict) -> dict:
    return {**r, "price": money.money_str(r["price_minor"], r["currency"]) if r.get("price_minor") is not None else None,
            "discount_pct": str(r["discount_pct"]) if r.get("discount_pct") is not None else None}


@router.get("/offers")
def list_offers(slug: str, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        cur.execute(f"SELECT {OFFER_COLS} FROM gst_journey_offers WHERE hotel_slug = %s ORDER BY active DESC, id DESC",
                    (slug,))
        return [_offer_out(dict(r)) for r in cur.fetchall()]


def _offer_values(body: OfferIn, currency: str) -> dict:
    return {"step": body.step, "title_en": body.title_en.strip(), "title_ar": (body.title_ar or "").strip() or None,
            "price_minor": money.to_minor(body.price, currency) if body.price is not None else None,
            "discount_pct": body.discount_pct, "currency": currency,
            "code_prefix": (body.code_prefix or "").upper() or None, "valid_from": body.valid_from, "valid_to": body.valid_to}


@router.post("/offers", status_code=201)
def create_offer(slug: str, body: OfferIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        v = _offer_values(body, ctx.hotel.currency)
        cur.execute(
            f"INSERT INTO gst_journey_offers (hotel_slug, {', '.join(v)}) VALUES (%s, {', '.join(['%s'] * len(v))}) "
            f"RETURNING {OFFER_COLS}",
            (slug, *v.values()),
        )
        row = dict(cur.fetchone())
        audit(cur, ctx, "guests.journeys.offer_create", "gst_journey_offer", row["id"], after={"step": row["step"]})
        return _offer_out(row)


@router.put("/offers/{offer_id}")
def update_offer(slug: str, offer_id: int, body: OfferIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """An edit withdraws the approval: the owner approves what goes out."""
    with db.db() as cur:
        v = _offer_values(body, ctx.hotel.currency)
        sets = ", ".join(f"{k} = %s" for k in v)
        cur.execute(f"UPDATE gst_journey_offers SET {sets}, approved_at = NULL, approved_by = NULL "
                    f"WHERE hotel_slug = %s AND id = %s RETURNING {OFFER_COLS}", (*v.values(), slug, offer_id))
        row = cur.fetchone()
        if row is None:
            raise ClefsError(404, "Offer not found", "offer_not_found")
        audit(cur, ctx, "guests.journeys.offer_update", "gst_journey_offer", offer_id)
        return _offer_out(dict(row))


@router.post("/offers/{offer_id}/approve")
def approve_offer(slug: str, offer_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        cur.execute(f"UPDATE gst_journey_offers SET approved_at = %s, approved_by = %s WHERE hotel_slug = %s AND id = %s "
                    f"AND active RETURNING {OFFER_COLS}", (clock.now(), ctx.principal.user_id, slug, offer_id))
        row = cur.fetchone()
        if row is None:
            raise ClefsError(404, "Offer not found", "offer_not_found")
        audit(cur, ctx, "guests.journeys.offer_approve", "gst_journey_offer", offer_id)
        return _offer_out(dict(row))


@router.delete("/offers/{offer_id}")
def deactivate_offer(slug: str, offer_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        cur.execute("UPDATE gst_journey_offers SET active = FALSE WHERE hotel_slug = %s AND id = %s", (slug, offer_id))
        if cur.rowcount == 0:
            raise ClefsError(404, "Offer not found", "offer_not_found")
        audit(cur, ctx, "guests.journeys.offer_deactivate", "gst_journey_offer", offer_id)
        return {"ok": True}


# ─── Reservation timeline ───


@router.get("/reservations/{reservation_id}/timeline")
def timeline(slug: str, reservation_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Every step, reply, free-form message, preference and feedback of one
    stay. Guest words (reply summaries, feedback text, requests) are masked
    as transcript for the caller's role (core.pii)."""
    from clefs.core import pii
    from clefs.modules.guests.journeys import planner

    with db.db(readonly=True) as cur:
        plans = planner.plans_for(cur, slug, reservation_id, live_only=False)
        cur.execute("SELECT id, plan_id, outcome, deterministic_hit, llm_used, summary_en, service_ref, action_id, created_at "
                    "FROM gst_journey_replies WHERE hotel_slug = %s AND reservation_id = %s ORDER BY created_at",
                    (slug, reservation_id))
        replies = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id, plan_id, kind, channel, sent_at FROM gst_journey_messages WHERE hotel_slug = %s "
                    "AND reservation_id = %s ORDER BY sent_at", (slug, reservation_id))
        messages = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT arrival_time, requests, occasion, language, submitted_at FROM gst_stay_prefs "
                    "WHERE hotel_slug = %s AND reservation_id = %s", (slug, reservation_id))
        prefs = cur.fetchone()
        cur.execute("SELECT overall, went_well, improve, contact_ok, language, followup_ref, submitted_at FROM gst_feedback "
                    "WHERE hotel_slug = %s AND reservation_id = %s", (slug, reservation_id))
        fb = cur.fetchone()
    out = {
        "reservation_id": reservation_id,
        "plans": [{k: p[k] for k in ("id", "step", "seq", "status", "skip_reason", "channel", "due_at", "valid_until",
                                     "last_attempt_at", "action_id", "attempt", "error_code", "category_at_send",
                                     "direct_code", "plan_version", "checkin_time_inferred")} for p in plans],
        "replies": replies, "messages": messages,
        "prefs": dict(prefs) if prefs else None, "feedback": dict(fb) if fb else None,
    }
    return pii.mask_for_ctx(out, ctx, field_classes={
        "replies.summary_en": pii.TRANSCRIPT, "prefs.requests": pii.TRANSCRIPT, "prefs.occasion": pii.TRANSCRIPT,
        "feedback.went_well": pii.TRANSCRIPT, "feedback.improve": pii.TRANSCRIPT,
    })


# ─── Settings (the journeys part of guests settings) ───

JOURNEY_KEYS = ("enabled_steps", "utility_window", "marketing_window", "j2_min_lead_days", "j5_offset_h", "j7_offset_days",
                "j8_offsets", "review_links", "j7_for_ota_provided_contacts", "standard_checkin_time",
                "standard_checkout_time", "ai_on_unconsented_inbound", "wa_billing_attested_at", "booking_engine_url",
                "planning_info", "checkin_bring", "directions")


def _settings_out(cfg: gsettings.GuestsSettings) -> dict:
    d = cfg.model_dump(mode="json")
    return {k: d[k] for k in JOURNEY_KEYS}


@router.get("/settings")
def get_settings_route(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return _settings_out(gsettings.load(cur, slug))


@router.put("/settings")
def put_settings(slug: str, body: dict, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Partial update of the journeys keys; everything is validated by
    GuestsSettings (the marketing window narrows only; review links are the
    official hosts only). The owner attests WABA billing with
    {"wa_billing_attested": true}."""
    from pydantic import ValidationError

    unknown = sorted(set(body) - set(JOURNEY_KEYS) - {"wa_billing_attested"})
    if unknown:
        raise ClefsError(400, f"unknown settings {unknown}", "unknown_setting")
    with db.db() as cur:
        current = get_module_settings(cur, slug, "guests", gsettings.GuestsSettings).model_dump(mode="json")
        patch = {k: v for k, v in body.items() if k in JOURNEY_KEYS}
        if "wa_billing_attested" in body:
            patch["wa_billing_attested_at"] = clock.now().isoformat() if body["wa_billing_attested"] else None
        try:
            cfg = gsettings.GuestsSettings.model_validate({**current, **patch})
        except ValidationError as exc:
            raise ClefsError(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
                             "invalid_settings") from exc
        set_module_settings(cur, slug, "guests", cfg.model_dump(mode="json"), actor=ctx)
        return _settings_out(cfg)
