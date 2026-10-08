"""
Sending through the ledger (04 spec 3.7): the executor `send_step`, the
shared `send_plan` (also used by the dispatcher for a deferred or 131049
re-send of an already approved action), variable filling and verifiers.

An executor result is the ledger's record of the send:
- template_id = meta_name@version, template_category = Meta's category,
- consent_basis = contract:reservation:<id> for utility (J7's basis is under
  legal review, 7.7) or consent:<consent id> for marketing,
- reversal_handle = {kind: none, reason: message_sent}.
"""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from clefs import notify
from clefs.auth import tokens as auth_tokens
from clefs.core import clock, money, pii, privacy
from clefs.modules.concierge import api as concierge_api
from clefs.modules.guests import contacts, identity, marketing, stays_port, timeutil
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import planner, steps, templates
from clefs.modules.guests.journeys import tokens as jtokens
from clefs.settings import get_settings

log = logging.getLogger("clefs.guests.journeys.executor")

GUEST_FALLBACK = {"en": "Guest", "ar": "ضيفنا"}
# Approved KB facts that feed the KB-sourced variables (3.6), in preference order.
KB_KEYS = {
    "planning_info": ("arrival_info", "arrival", "planning_info", "check_in_time"),
    "checkin_bring": ("id_policy", "check_in_documents", "checkin_bring"),
    "directions": ("directions", "how_to_reach", "address"),
}
DEFAULT_BRING = {"en": "a passport or Emirates ID for each guest", "ar": "جواز سفر أو هوية إماراتية لكل ضيف"}


# ─── Loading ───


def load_plan(cur: Any, plan_id: int, *, lock: bool = False) -> dict | None:
    cur.execute(f"SELECT {planner.PLAN_COLS} FROM gst_journey_plans WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                (plan_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def load_stay(cur: Any, hotel_slug: str, reservation_id: int) -> dict | None:
    """The snapshot, refreshed from core first (status, room, dates)."""
    from clefs.modules.guests import linking

    return linking.link_reservation(cur, hotel_slug, reservation_id) or contacts.stay_for_reservation(
        cur, hotel_slug, reservation_id)


def set_plan(cur: Any, plan_id: int, **changes: Any) -> dict:
    changes["updated_at"] = clock.now()
    sets = ", ".join(f"{k} = %s" for k in changes)
    cur.execute(f"UPDATE gst_journey_plans SET {sets} WHERE id = %s RETURNING {planner.PLAN_COLS}",
                (*changes.values(), plan_id))
    return dict(cur.fetchone())


# ─── Variables ───


def _date(d: Any, lang: str) -> str:
    d = stays_port.as_date(d)
    if d is None:
        return "-"
    return d.strftime("%d/%m/%Y") if lang == "ar" else d.strftime("%d %b %Y").lstrip("0")


def _kb(cur: Any, hotel_slug: str, source: str, lang: str) -> str | None:
    try:
        facts = concierge_api.kb_facts(cur, hotel_slug, language=lang, keyed_only=True)
    except Exception:
        return None
    by_key = {f.get("fact_key"): f for f in facts if f.get("approved_at") and f.get("fact_key")}
    for k in KB_KEYS.get(source, ()):
        f = by_key.get(k)
        text = (f or {}).get("text") or (f or {}).get("value")
        if text and len(str(text)) <= 200:
            return str(text).strip()
    return None


def booking_url(hotel: Any, cfg: gsettings.GuestsSettings, *, channel: str, plan_id: int) -> str:
    """J8 {{6}}: the booking engine with utm_campaign=gst_j8 and
    utm_content=<plan_id>, which the J8 verifier matches (3.7)."""
    base = cfg.booking_engine_url or getattr(hotel, "website", None) or get_settings().web_base_url
    if not urlparse(base).scheme:
        base = "https://" + base
    u = urlparse(base)
    q = dict(parse_qsl(u.query))
    q.update({"utm_source": "clefs", "utm_medium": channel, "utm_campaign": "gst_j8", "utm_content": str(plan_id)})
    return urlunparse(u._replace(query=urlencode(q)))


def guest_first_name(cur: Any, hotel_slug: str, stay: dict) -> str | None:
    r = stays_port.backend().reservation(cur, hotel_slug, stay["reservation_id"])
    return pii.first_name((r or {}).get("guest_name")) if r else None


def fill(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, plan: dict, stay: dict, tpl: dict, *,
         offer: dict | None, channel: str, links: dict[str, str]) -> list[str]:
    """Values for {{1}}..{{n}} in template order, each within its max."""
    lang = tpl["language"]
    out: list[str] = []
    for v in tpl.get("variables") or []:
        src = v["source"]
        val: Any
        if src == "first_name":
            val = guest_first_name(cur, hotel.slug, stay) or GUEST_FALLBACK[lang]
        elif src == "hotel_name":
            val = hotel.name
        elif src in ("check_in", "check_out"):
            val = _date(stay.get(src), lang)
        elif src == "booking_ref":
            val = stay.get("booking_ref") or str(stay["reservation_id"])
        elif src == "room":
            val = stay.get("room") or f"booking {stay.get('booking_ref') or stay['reservation_id']}"
        elif src == "checkin_time":
            val = cfg.standard_checkin_time
        elif src == "planning_info":
            val = _kb(cur, hotel.slug, src, lang) or cfg.planning_info or \
                f"check-in from {cfg.standard_checkin_time}, check-out by {cfg.standard_checkout_time}"
        elif src == "checkin_bring":
            val = _kb(cur, hotel.slug, src, lang) or cfg.checkin_bring or DEFAULT_BRING[lang]
        elif src == "directions":
            val = _kb(cur, hotel.slug, src, lang) or cfg.directions or getattr(hotel, "address", None) or \
                f"{hotel.name}, {hotel.city or ''}".strip(", ")
        elif src == "offer_title":
            val = ((offer or {}).get("title_ar") if lang == "ar" else None) or (offer or {}).get("title_en") or "-"
        elif src == "offer_price":
            o = offer or {}
            val = money.fmt_money(o["price_minor"], o["currency"], minor=True) if o.get("price_minor") is not None \
                else (f"{o['discount_pct']}%" if o.get("discount_pct") is not None else "-")
        elif src == "direct_code":
            val = plan.get("direct_code") or "-"
        elif src == "valid_to":
            val = _date((offer or {}).get("valid_to"), lang)
        elif src == "booking_url":
            val = booking_url(hotel, cfg, channel=channel, plan_id=plan["id"])
        else:
            val = links.get(src) or "-"
        out.append(str(val)[: int(v.get("max") or 1024)] or "-")
    return out


def render(body: str, values: list[str]) -> str:
    for i, v in enumerate(values, start=1):
        body = body.replace("{{%d}}" % i, v)
    return body


# ─── Links and recipients ───


def links_for(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, plan: dict, stay: dict) -> dict[str, str]:
    """Tokens behind the J1 and J7 URL buttons (3.2 expiry rules)."""
    out: dict[str, str] = {}
    if plan["step"] == "j1":
        ci = stays_port.as_date(stay.get("check_in")) or timeutil.local(hotel, clock.now()).date()
        out["prefs_token"] = jtokens.mint(cur, hotel.slug, stay["reservation_id"], "prefs",
                                          expires_at=timeutil.at_local(hotel, ci, time(23, 59)),
                                          ref={"plan_id": plan["id"]})
    elif plan["step"] == "j7":
        out["feedback_token"] = jtokens.mint(cur, hotel.slug, stay["reservation_id"], "feedback",
                                             expires_at=plan["valid_until"], ref={"plan_id": plan["id"]})
    return out


def page_url(path: str) -> str:
    return get_settings().web_base_url.rstrip("/") + path


def recipient(cur: Any, point: dict) -> str | None:
    """Unseal the address: the point's own value (guest_direct, guest_stay)
    or core's (pms_*, ota_alias) through core.stays.contact_value, audited."""
    if point.get("value_enc") is None:
        cur.execute("SELECT value_enc FROM gst_contact_points WHERE id = %s", (point["id"],))
        row = cur.fetchone()
        point = {**point, "value_enc": row["value_enc"] if row else None}
    if point.get("value_enc") is not None:
        return privacy.unseal(bytes(point["value_enc"]))
    if point.get("reservation_contact_id"):
        return stays_port.backend().contact_value(cur, point["reservation_contact_id"])
    return None


def _wa_buttons(tpl: dict, plan: dict, links: dict[str, str]) -> list[dict]:
    out = []
    for b in tpl.get("buttons") or []:
        if b.get("type") == "URL":
            out.append({"type": "url", "text": links.get(b.get("var") or "", "-")})
        elif b.get("type") == "QUICK_REPLY":
            # Signed: only a verified ref is trusted as a button answer (F4).
            out.append({"type": "quick_reply", "payload": auth_tokens.sign_ref(f"gstj:{plan['id']}:{b['payload']}")})
    return out


def _email(hotel: Any, tpl: dict, text: str, links: dict[str, str], *, unsubscribe: str | None) -> tuple[str, str, str]:
    """(subject, html, text) for the email variant: same content, plus the
    page link, and for marketing a footer unsubscribe link."""
    subject = (tpl.get("subject") or hotel.name).replace("{hotel}", hotel.name)
    rtl = ' dir="rtl"' if tpl["language"] == "ar" else ""
    parts = [f"<p{rtl}>{html.escape(text)}</p>"]
    plain = [text]
    for b in tpl.get("buttons") or []:
        if b.get("type") == "URL" and links.get(b.get("var") or ""):
            url = page_url(b["path"].replace("{{1}}", links[b["var"]]))
            parts.append(f'<p{rtl}><a href="{html.escape(url)}">{html.escape(b["text"])}</a></p>')
            plain.append(f"{b['text']}: {url}")
    if unsubscribe:
        label = "إلغاء الاشتراك" if tpl["language"] == "ar" else "Unsubscribe"
        parts.append(f'<p{rtl} style="font-size:12px"><a href="{html.escape(unsubscribe)}">{label}</a></p>')
        plain.append(f"{label}: {unsubscribe}")
    return subject, "\n".join(parts), "\n\n".join(plain)


# ─── send_plan ───


def purpose_of(step: str) -> str:
    return f"guests.journey.{step}"


def send_plan(cur: Any, hotel: Any, plan: dict, *, action_id: int | None, attempt: int,
              now: datetime | None = None, only: tuple[str, ...] | None = None) -> dict:
    """Run the guards again and send one step. Writes the plan's outcome:
    sent | deferred | skipped | observed | failed. Idempotent per
    (plan, attempt): the outbox key is gst:{plan_id}:a{attempt}."""
    from clefs.modules.guests import optout
    from clefs.modules.guests.journeys import guards

    now = now or clock.now()
    cfg = gsettings.load(cur, hotel.slug)
    stay = load_stay(cur, hotel.slug, plan["reservation_id"])
    v = guards.check(cur, hotel, cfg, plan, stay, now=now, only=only)
    if v.skip:
        set_plan(cur, plan["id"], status="skipped", skip_reason=v.reason, action_id=action_id or plan["action_id"])
        return {"status": "skipped", "reason": v.reason}
    if v.defer_to is not None:
        set_plan(cur, plan["id"], status="deferred", due_at=v.defer_to, skip_reason=v.reason,
                 action_id=action_id or plan["action_id"], claimed_at=None)
        return {"status": "deferred", "reason": v.reason, "until": v.defer_to}
    choice = v.resolution.choice if v.resolution else None
    if choice is None:
        set_plan(cur, plan["id"], status="observed", skip_reason="template_missing", action_id=action_id or plan["action_id"])
        return {"status": "observed", "reason": "template_missing"}
    tpl, step = choice.template, plan["step"]
    links = links_for(cur, hotel, cfg, plan, stay)
    values = fill(cur, hotel, cfg, plan, stay, tpl, offer=v.offer, channel=choice.channel, links=links)
    bad = templates.lint_values(values, category=choice.category, step=step)
    if bad:
        set_plan(cur, plan["id"], status="skipped", skip_reason=f"lint_{bad[0]}")
        return {"status": "skipped", "reason": f"lint_{bad[0]}"}
    to = recipient(cur, choice.point)
    if not to:
        set_plan(cur, plan["id"], status="skipped", skip_reason="no_eligible_contact")
        return {"status": "skipped", "reason": "no_eligible_contact"}
    is_mkt = choice.category == "MARKETING"
    key = f"gst:{plan['id']}:a{attempt}"
    h = bytes(choice.point["value_hmac"])
    if choice.channel == "whatsapp":
        res = notify.send_whatsapp_template(
            to, tpl["meta_name"], tpl["language"], values, hotel_slug=hotel.slug, purpose=purpose_of(step),
            sender="hotel", buttons=_wa_buttons(tpl, plan, links), category="marketing" if is_mkt else "utility",
            idempotency_key=key, action_id=action_id, cur=cur,
        )
    else:
        unsub = None
        headers = None
        if is_mkt:
            unsub = get_settings().PUBLIC_API_ORIGIN.rstrip("/") + optout.unsubscribe_path(
                hotel.slug, identity_hmac=h, channel="email")
            headers = {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        subject, body_html, body_text = _email(hotel, tpl, render(tpl["body"], values), links, unsubscribe=unsub)
        res = notify.send_email(to, subject, body_html, text=body_text, headers=headers, hotel_slug=hotel.slug,
                                purpose=purpose_of(step), category="marketing" if is_mkt else "utility",
                                idempotency_key=key, action_id=action_id, cur=cur)
    if not res.ok:
        set_plan(cur, plan["id"], status="failed", error_code=(res.status or "send_failed")[:40],
                 channel=choice.channel, contact_point_id=choice.point["id"], action_id=action_id or plan["action_id"])
        return {"status": "failed", "reason": res.status, "error": res.error}
    set_plan(cur, plan["id"], status="sent", skip_reason=None, channel=choice.channel, contact_point_id=choice.point["id"],
             template_id=tpl["id"], offer_id=(v.offer or {}).get("id"), outbound_message_id=res.outbox_id,
             attempt=attempt, last_attempt_at=now, category_at_send=choice.category,
             action_id=action_id or plan["action_id"], claimed_at=None)
    cur.execute("UPDATE gst_contact_points SET last_used_at = %s WHERE id = %s", (now, choice.point["id"]))
    from clefs.modules.guests.journeys import status as jstatus

    jstatus.replay(cur, res.outbox_id)
    after = load_plan(cur, plan["id"]) or {}
    if after.get("status") == "failed":
        return {"status": "failed", "reason": after.get("error_code")}
    if is_mkt:
        marketing.record_send(cur, hotel.slug, identity_hmacs=[h], channel=choice.channel,
                              source=steps.SEND_SOURCE.get(step, "other"), sent_at=now, ref=f"plan:{plan['id']}",
                              phase=steps.phase_of(step), reservation_id=plan["reservation_id"])
    return {"status": "sent", "channel": choice.channel, "template": tpl, "category": choice.category,
            "consent_id": choice.consent_id, "outbox_id": res.outbox_id, "values": values}


# ─── The executor ───

from pydantic import BaseModel  # noqa: E402

from clefs.core import actions  # noqa: E402


def basis(plan: dict, out: dict) -> str:
    if out.get("category") == "MARKETING" and out.get("consent_id"):
        return f"consent:{out['consent_id']}"
    return f"contract:reservation:{plan['reservation_id']}"


def send_step(ctx: actions.ActionCtx) -> actions.ExecResult:
    """Every journey step type's executor. Idempotent on plan_id: a plan
    already sent under this action is reported again, never re-sent."""
    cur = ctx.cur
    plan = load_plan(cur, int(ctx.params["plan_id"]), lock=True)
    if plan is None:
        return actions.ExecResult(ok=False, error="plan_missing")
    if plan["status"] in steps.LIVE_SENT and plan["action_id"] == ctx.action_id:
        tpl = templates.get_template(cur, ctx.hotel_slug, plan["template_id"]) if plan["template_id"] else None
        return actions.ExecResult(
            data={"plan_id": plan["id"], "already_sent": True}, template_id=templates.template_ref(tpl) if tpl else None,
            template_category=plan["category_at_send"], consent_basis=f"contract:reservation:{plan['reservation_id']}",
            reversal_handle={"kind": "none", "reason": "message_sent"},
        )
    if plan["status"] in ("cancelled", "skipped", "failed", "done"):
        return actions.ExecResult(ok=False, error=f"plan_{plan['status']}")
    out = send_plan(cur, ctx.hotel, plan, action_id=ctx.action_id, attempt=max(1, plan["attempt"]))
    if out["status"] == "sent":
        return actions.ExecResult(
            data={"plan_id": plan["id"], "channel": out["channel"], "outbox_id": out["outbox_id"]},
            template_id=templates.template_ref(out["template"]), template_category=out["category"],
            consent_basis=basis(plan, out), reversal_handle={"kind": "none", "reason": "message_sent"},
        )
    if out["status"] == "deferred":
        # Approved but outside its window (or a cap): the dispatcher sends it
        # at `until` under this same action, with no new approval.
        return actions.ExecResult(data={"plan_id": plan["id"], "deferred_to": out["until"].isoformat(),
                                        "reason": out["reason"]},
                                  reversal_handle={"kind": "none", "reason": "not_sent_yet"})
    return actions.ExecResult(ok=False, error=out.get("reason") or out["status"])


def gate(ctx: actions.ActionCtx) -> actions.Gate:
    """3.4/3.5 template gate: at least one eligible channel with an approved
    template, else observe. J3/J8 also need an approved, live offer."""
    cur = ctx.cur
    plan = load_plan(cur, int(ctx.params.get("plan_id") or 0))
    if plan is None:
        return actions.Gate(max_level="observe", reasons=["plan missing"])
    stay = contacts.stay_for_reservation(cur, ctx.hotel_slug, plan["reservation_id"])
    if stay is None:
        return actions.Gate(max_level="observe", reasons=["stay unknown"])
    from clefs.modules.guests.journeys import channels, guards

    cfg = gsettings.load(cur, ctx.hotel_slug)
    step = plan["step"]
    if step in steps.MARKETING_STEPS and guards.live_offer(cur, ctx.hotel_slug, step,
                                                          timeutil.local(ctx.hotel, clock.now()).date()) is None:
        return actions.Gate(max_level="observe", reasons=["no approved, live offer"])
    res = channels.resolve(cur, ctx.hotel, cfg, stay, step, at=clock.now(), sync=False)
    ceiling = "auto_within_limits" if step in steps.MARKETING_STEPS else "auto"
    return actions.template_gate(res.channels, open_level=ceiling)


# ─── Limits (J3, J8: 04 spec 1.4) ───


class OfferLimits(BaseModel):
    """The only per-day send cap on offers (offers carry none)."""

    max_sends_per_day: int | None = None
    max_offer_price_minor: int | None = None


def offer_limits(ctx: actions.ActionCtx, limits: OfferLimits) -> actions.LimitCheck:
    if limits.max_sends_per_day is None and limits.max_offer_price_minor is None:
        return actions.LimitCheck(ok=False, reasons=["no limits set"])
    reasons: list[str] = []
    if limits.max_sends_per_day is not None:
        d0, d1 = timeutil.day_bounds(ctx.hotel, clock.now())
        ctx.cur.execute(
            "SELECT COUNT(*) AS n FROM actions WHERE hotel_slug = %s AND type = %s AND executed_at >= %s AND executed_at < %s",
            (ctx.hotel_slug, ctx.type, d0, d1),
        )
        if int(ctx.cur.fetchone()["n"]) >= limits.max_sends_per_day:
            reasons.append(f"{limits.max_sends_per_day} sends today already")
    if limits.max_offer_price_minor is not None:
        price = ctx.params.get("offer_price_minor")
        if price is not None and int(price) > limits.max_offer_price_minor:
            reasons.append(f"offer price {price} exceeds max_offer_price_minor {limits.max_offer_price_minor}")
    return actions.LimitCheck(ok=not reasons, reasons=reasons)


# ─── Verifiers (3.7 step 7) ───


def _money0(ctx: actions.ActionCtx, status: str, evidence: list[dict], *, attribution: str = "observed") -> actions.VerifyResult:
    return actions.VerifyResult(status=status, outcome_minor=0, currency=ctx.hotel.currency,
                                counts_toward_verified_profit=False, attribution=attribution, evidence=evidence)


def utility_verifier(ctx: actions.ActionCtx) -> actions.VerifyResult:
    """Utility steps and J3: verified when delivered within 48 h of the last
    attempt (a 131049 re-send restarts the 48 h). No money: J3's money is on
    guests.offer_accept."""
    plan = load_plan(ctx.cur, int(ctx.params["plan_id"]))
    if plan is None:
        return _money0(ctx, "not_verified", [{"kind": "row", "label": "plan missing"}])
    ev = [{"kind": "row", "label": "journey plan", "value": plan["status"], "ref": {"plan_id": plan["id"]}}]
    if plan["status"] in ("delivered", "read", "replied"):
        return _money0(ctx, "verified", ev)
    if plan["status"] in ("skipped", "cancelled", "failed", "observed"):
        return _money0(ctx, "not_verified", ev)
    last = plan["last_attempt_at"]
    if plan["status"] == "sent" and last is not None and clock.now() > last + steps.UTILITY_VERIFY_WINDOW:
        return _money0(ctx, "not_verified", ev)
    return _money0(ctx, "pending", ev)


def _send_cost_minor(cur: Any, plan_id: int, currency: str) -> int:
    cur.execute("SELECT COALESCE(SUM(cost_micros), 0) AS m FROM gst_message_costs WHERE plan_id = %s", (plan_id,))
    micros = int(cur.fetchone()["m"])
    return int(round(micros * 10 ** money.minor_units(currency) / 1_000_000))


def _j8_matches(cur: Any, hotel_slug: str, plan: dict, h: bytes, since: datetime) -> list[dict]:
    """Direct reservations for the same identity booked after the send: by
    core's own contact rows, or by the guest's direct points on them."""
    cur.execute("SELECT to_regclass('public.reservations') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return []
    cur.execute(
        f"""
        SELECT DISTINCT r.id, r.channel_code, r.check_in, r.promo_code, r.utm, r.room_revenue_minor, r.gross_minor, r.currency
        FROM reservations r
        WHERE r.hotel_slug = %s AND r.id <> %s AND r.status <> 'CANCELLED'
          AND UPPER(COALESCE(r.channel_code, '')) = ANY(%s)
          AND COALESCE(r.booked_at, r.created_at) >= %s AND COALESCE(r.booked_at, r.created_at) < %s
          AND (EXISTS (SELECT 1 FROM reservation_contacts c WHERE c.reservation_id = r.id AND c.value_hmac = %s)
               OR EXISTS (SELECT 1 FROM gst_contact_points p WHERE p.reservation_id = r.id AND p.value_hmac = %s))
        ORDER BY r.id
        """,
        (hotel_slug, plan["reservation_id"], list(stays_port.DIRECT_CHANNELS), since, since + timedelta(days=30), h, h),
    )
    return [dict(r) for r in cur.fetchall()]


def j8_verifier(ctx: actions.ActionCtx) -> actions.VerifyResult:
    """3.7: a direct booking by the same identity within 30 days. It counts
    toward verified profit only when THIS plan's code was redeemed on it, or
    the J8 link (utm_campaign=gst_j8, utm_content=plan_id) was used;
    a match on the hmac alone is `influenced`."""
    from clefs.modules.profit_guard import api as pg_api

    cur = ctx.cur
    plan = load_plan(cur, int(ctx.params["plan_id"]))
    if plan is None or plan["contact_point_id"] is None or plan["last_attempt_at"] is None:
        return _money0(ctx, "pending", [])
    cur.execute("SELECT value_hmac FROM gst_contact_points WHERE id = %s", (plan["contact_point_id"],))
    row = cur.fetchone()
    if row is None or row["value_hmac"] is None:
        return _money0(ctx, "pending", [])
    matches = _j8_matches(cur, ctx.hotel_slug, plan, bytes(row["value_hmac"]), plan["last_attempt_at"])
    if not matches:
        return _money0(ctx, "pending", [])
    code = (plan.get("direct_code") or "").upper()
    caused = next((m for m in matches if code and (m.get("promo_code") or "").strip().upper() == code), None) or next(
        (m for m in matches if (m.get("utm") or {}).get("utm_campaign") == "gst_j8"
         and str((m.get("utm") or {}).get("utm_content")) == str(plan["id"])), None)
    if caused is None:
        return _money0(ctx, "verified", [{"kind": "row", "label": "direct booking, same guest (hmac only)",
                                          "ref": {"reservation_id": matches[0]["id"]}}], attribution="influenced")
    if code and (caused.get("promo_code") or "").strip().upper() == code:
        identity.redeem_direct_code(cur, ctx.hotel_slug, code, reservation_id=caused["id"],
                                    on=stays_port.as_date(caused["check_in"]) or clock.now().date())
    ccy = ctx.hotel.currency
    prev = stays_port.backend().reservation(cur, ctx.hotel_slug, plan["reservation_id"]) or {}
    commission = 0
    if stays_port.is_ota(prev.get("channel_code")):
        br = stays_port.backend().reservation_breakdown(cur, caused["id"])
        ec = pg_api.expected_commission(cur, ctx.hotel_slug, ota_code=prev["channel_code"], breakdown=br,
                                        on=stays_port.as_date(caused["check_in"]))
        commission = int((ec or {}).get("commission_minor") or 0)
    cur.execute("SELECT discount_pct FROM gst_direct_codes WHERE plan_id = %s", (plan["id"],))
    d = cur.fetchone()
    base = caused.get("room_revenue_minor") or caused.get("gross_minor") or 0
    discount = int(round(float(d["discount_pct"]) * base / 100)) if d and d["discount_pct"] is not None else 0
    dist = int(pg_api.distribution_cost_for_reservation(cur, ctx.hotel_slug, reservation_id=caused["id"]) or 0)
    outcome = commission - discount - dist - _send_cost_minor(cur, plan["id"], ccy)
    from clefs.core import signals

    signals.upsert_signal(cur, ctx.hotel_slug, signals.SignalIn(
        module="guests", confidence=1.0, kind="guests.j8_conversion_verified", dedupe_key=f"guests.j8_conversion_verified:{plan['id']}",
        title=f"A guest rebooked direct with their rebooking offer (booking {caused['id']})",
        impact_minor=outcome, currency=ccy, impact_kind="verified_profit",
        evidence=[signals.Evidence(kind="row", label="direct reservation", ref={"reservation_id": caused["id"]})],
        subject_type="reservation", subject_id=str(caused["id"]),
    ))
    return actions.VerifyResult(
        status="verified", outcome_minor=outcome, currency=ccy, counts_toward_verified_profit=True, attribution="caused",
        evidence=[{"kind": "calc", "label": "commission avoided - discount - distribution - send cost",
                   "value": f"{commission} - {discount} - {dist} - send", "ref": {"reservation_id": caused["id"]}}],
    )


# ─── Registration (04 spec 1.4 defaults and ceilings) ───


def _register() -> None:
    for step, key in steps.ACTION_TYPES.items():
        is_mkt = step in steps.MARKETING_STEPS
        actions.register_action_type(actions.ActionType(
            key=key, module="guests", label=steps.LABELS[step],
            executor=send_step,
            verifier=j8_verifier if step == "j8" else utility_verifier,
            verify_after=timedelta(days=1) if step == "j8" else timedelta(hours=1),
            verify_every=timedelta(days=1) if step == "j8" else timedelta(hours=6),
            verify_until=timedelta(days=30) if step == "j8" else timedelta(days=4),
            default_owner_role="gm",
            default_level="auto_within_limits" if is_mkt else "auto",
            max_level="auto_within_limits" if is_mkt else "auto",
            limits_model=OfferLimits if is_mkt else None,
            limit_checker=offer_limits if is_mkt else None,
            gate=gate,
            decide_perm="guests.journeys.manage",
            counts_toward_verified_profit=(step == "j8"),
            description=f"Guest journey step {step.upper()}: {steps.LABELS[step]}",
        ))
    # Human tasks: no executor; staff mark them done.
    actions.register_action_type(actions.ActionType(
        key=steps.CHECKOUT_RECOVERY, module="guests", label="Check-out recovery", default_owner_role="gm",
        default_level="approve", max_level="approve", decide_perm="guests.journeys.manage",
        description="A guest departs with open issues: attempt recovery at check-out. Never mention reviews.",
    ))
    actions.register_action_type(actions.ActionType(
        key=steps.OFFER_ACCEPT, module="guests", label="Add accepted offer to booking", default_owner_role="front_desk",
        default_level="approve", max_level="approve", decide_perm="guests.journeys.view",
        verifier=offer_accept_verifier, verify_after=timedelta(minutes=1),
        description="The guest replied YES to a J3 offer: add it to the booking.",
    ))


def offer_accept_verifier(ctx: actions.ActionCtx) -> actions.VerifyResult:
    """Verified when staff mark the task done: the offer price, labelled
    upsell revenue, never counted toward verified profit (3.8)."""
    price = ctx.params.get("offer_price_minor")
    return actions.VerifyResult(
        status="verified", outcome_minor=int(price or 0), currency=ctx.params.get("currency") or ctx.hotel.currency,
        counts_toward_verified_profit=False, attribution="caused",
        evidence=[{"kind": "row", "label": "upsell revenue (offer accepted)", "ref": {"plan_id": ctx.params.get("plan_id")}}],
    )


_register()
