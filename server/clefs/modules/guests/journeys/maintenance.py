"""
Journeys housekeeping jobs (04 spec 3.10):

- guests.journey_contact_purge (03:40): points past usable_until are purged
  (value_enc deleted; value_hmac nulled for pms_ota, ota_alias, guest_stay),
  guest_direct points unused for 730 days too (their evidence files go).
- guests.optout_monitor (hourly): attributes STOPs to the journey step they
  answered and demotes a step whose opt-out rate spikes (1.5).
- guests.template_category_check (daily): Meta category and quality.
- guests.wa_billing_check (daily): WABA payment-method readiness (3.5).
- guests.journey_feedback_expire (daily): feedback and consent links past
  expires_at are closed, so their pages say "This link has expired".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from clefs import jobs
from clefs.core import actions, clock, signals
from clefs.core.hotels import get_hotel
from clefs.modules.guests import evidence as evidence_mod
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import channels, steps, templates

log = logging.getLogger("clefs.guests.journeys.maintenance")

GUEST_DIRECT_IDLE = timedelta(days=730)
OPTOUT_MIN = 3
OPTOUT_RATE = 0.05


# ─── Contact purge ───


def purge_contacts(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> dict:
    now = now or clock.now()
    cur.execute(
        """
        UPDATE gst_contact_points SET status = 'purged', value_enc = NULL, purged_at = %s, evidence = '{}'::jsonb,
            value_hmac = CASE WHEN provenance IN ('pms_ota', 'ota_alias', 'guest_stay') THEN NULL ELSE value_hmac END
        WHERE hotel_slug = %s AND status <> 'purged' AND usable_until IS NOT NULL AND usable_until <= %s
          AND provenance <> 'guest_direct'
        """,
        (now, hotel_slug, now),
    )
    expired = cur.rowcount
    cur.execute(
        "SELECT id, evidence FROM gst_contact_points WHERE hotel_slug = %s AND provenance = 'guest_direct' "
        "AND status <> 'purged' AND last_used_at < %s",
        (hotel_slug, now - GUEST_DIRECT_IDLE),
    )
    idle = [dict(r) for r in cur.fetchall()]
    files_deleted = sum(evidence_mod.delete_files(cur, hotel_slug, (r["evidence"] or {}).get("capture")) for r in idle)
    if idle:
        cur.execute(
            "UPDATE gst_contact_points SET status = 'purged', value_enc = NULL, purged_at = %s, evidence = '{}'::jsonb "
            "WHERE id = ANY(%s)",
            (now, [r["id"] for r in idle]),
        )
    cur.execute("DELETE FROM gst_stay_prefs WHERE hotel_slug = %s AND purge_after <= %s", (hotel_slug, now))
    return {"expired": expired, "idle_direct": len(idle), "evidence_files": files_deleted, "prefs": cur.rowcount}


@jobs.scheduled("guests.journey_contact_purge", jobs.daily_at("03:40"), timeout_s=240)
def purge_job(ctx: jobs.JobCtx) -> dict:
    return purge_contacts(ctx.cur, ctx.hotel_slug)


# ─── Feedback and consent link expiry ───


def expire_links(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> int:
    cur.execute(
        "UPDATE gst_public_tokens SET used_at = %s WHERE hotel_slug = %s AND purpose IN ('feedback', 'consent') "
        "AND used_at IS NULL AND expires_at <= %s",
        (now or clock.now(), hotel_slug, now or clock.now()),
    )
    return cur.rowcount


@jobs.scheduled("guests.journey_feedback_expire", jobs.daily_at("02:15"), timeout_s=60)
def expire_job(ctx: jobs.JobCtx) -> dict:
    return {"expired": expire_links(ctx.cur, ctx.hotel_slug)}


# ─── Opt-out monitor (3.10; 1.5 optout_spike) ───


def _plan_by_wamid(cur: Any, hotel_slug: str, wamid: str | None) -> dict | None:
    if not wamid:
        return None
    cur.execute("SELECT id, step FROM gst_journey_plans WHERE hotel_slug = %s AND wamid = %s", (hotel_slug, wamid))
    r = cur.fetchone()
    return dict(r) if r else None


def attribute_optouts(cur: Any, hotel_slug: str, *, now: datetime, lookback: timedelta = timedelta(days=7)) -> int:
    """A STOP counts against the step of the journey message it replied to
    (context.id), else the latest journey send to that contact within 72 h
    before it. Otherwise it is unattributed and counts for no step."""
    cur.execute(
        "SELECT value_hmac, created_at, source FROM gst_suppressions WHERE hotel_slug = %s "
        "AND reason IN ('stop', 'complaint_about_messaging') AND created_at >= %s",
        (hotel_slug, now - lookback),
    )
    rows = [dict(r) for r in cur.fetchall()]
    n = 0
    for s in rows:
        h, t = bytes(s["value_hmac"]), s["created_at"]
        cur.execute(
            "SELECT payload FROM inbound_messages WHERE hotel_slug = %s AND sender_hmac = %s AND received_at <= %s "
            "AND received_at >= %s ORDER BY received_at DESC LIMIT 1",
            (hotel_slug, h, t + timedelta(minutes=1), t - timedelta(hours=1)),
        )
        msg = cur.fetchone()
        ctx_id = (((msg or {}).get("payload") or {}).get("context") or {}).get("id") if msg else None
        plan, basis = _plan_by_wamid(cur, hotel_slug, ctx_id), "context"
        if plan is None:
            cur.execute(
                """
                SELECT pl.id, pl.step FROM gst_journey_plans pl JOIN gst_contact_points cp ON cp.id = pl.contact_point_id
                WHERE pl.hotel_slug = %s AND cp.value_hmac = %s AND pl.last_attempt_at <= %s AND pl.last_attempt_at >= %s
                  AND pl.status IN ('sent', 'delivered', 'read', 'replied')
                ORDER BY pl.last_attempt_at DESC LIMIT 1
                """,
                (hotel_slug, h, t, t - steps.OPTOUT_ATTRIBUTION),
            )
            r = cur.fetchone()
            plan, basis = (dict(r) if r else None), "recent_send"
        if plan is None or plan["step"] not in steps.ACTION_TYPES:
            continue
        cur.execute(
            "INSERT INTO gst_journey_optouts (hotel_slug, value_hmac, step, plan_id, basis, source, opted_out_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (hotel_slug, h, plan["step"], plan["id"], basis, (s["source"] or "stop")[:60], t),
        )
        n += cur.rowcount
    return n


def optout_rates(cur: Any, hotel_slug: str, *, now: datetime) -> dict[str, dict]:
    """Per step: attributed opt-outs / delivered sends, over 7 and 28 days."""
    out: dict[str, dict] = {}
    for days in (7, 28):
        since = now - timedelta(days=days)
        cur.execute("SELECT step, COUNT(*) AS n FROM gst_journey_optouts WHERE hotel_slug = %s AND opted_out_at >= %s "
                    "GROUP BY step", (hotel_slug, since))
        opt = {r["step"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute("SELECT step, COUNT(*) AS n FROM gst_journey_plans WHERE hotel_slug = %s AND last_attempt_at >= %s "
                    "AND status IN ('delivered', 'read', 'replied') GROUP BY step", (hotel_slug, since))
        sent = {r["step"]: int(r["n"]) for r in cur.fetchall()}
        for step in steps.ACTION_TYPES:
            o, d = opt.get(step, 0), sent.get(step, 0)
            out.setdefault(step, {})[f"d{days}"] = {"optouts": o, "delivered": d, "rate": (o / d) if d else 0.0}
    return out


def _recently_demoted(cur: Any, hotel_slug: str, action_type: str, now: datetime) -> bool:
    cur.execute(
        "SELECT 1 FROM action_autonomy_events WHERE hotel_slug = %s AND action_type = %s AND kind = 'demoted' "
        "AND trigger = 'optout_spike' AND at >= %s LIMIT 1",
        (hotel_slug, action_type, now - timedelta(days=7)),
    )
    return cur.fetchone() is not None


def monitor_optouts(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> dict:
    """Types over threshold (7 d rate >= 5% with >= 3 opt-outs, or 7 d rate
    >= 2x the 28 d rate with >= 3 opt-outs) are passed to report_incident;
    only those types, and once per 7 days each."""
    now = now or clock.now()
    attributed = attribute_optouts(cur, hotel_slug, now=now)
    rates = optout_rates(cur, hotel_slug, now=now)
    spiking = []
    for step, r in rates.items():
        w, m = r["d7"], r["d28"]
        if w["optouts"] < OPTOUT_MIN:
            continue
        if w["rate"] >= OPTOUT_RATE or (m["rate"] > 0 and w["rate"] >= 2 * m["rate"]):
            key = steps.ACTION_TYPES[step]
            if not _recently_demoted(cur, hotel_slug, key, now):
                spiking.append((step, key, w))
    if spiking:
        actions.report_incident(cur, hotel_slug, kind="optout_spike", action_types=[k for _, k, _ in spiking],
                                evidence=[{"kind": "metric", "label": f"{s.upper()} opt-outs, 7 days",
                                           "value": f"{w['optouts']}/{w['delivered']}"} for s, _, w in spiking])
        week = now.isocalendar()
        signals.upsert_signal(cur, hotel_slug, signals.SignalIn(
            module="guests", confidence=1.0, kind="guests.optout_spike", dedupe_key=f"guests.optout_spike:{week.year}-W{week.week:02d}",
            title=f"Opt-outs spiked on {', '.join(s.upper() for s, _, _ in spiking)}; those steps now need approval",
            evidence=[signals.Evidence(kind="metric", label=f"{s.upper()} opt-outs, 7 days",
                                       value=f"{w['optouts']}/{w['delivered']}") for s, _, w in spiking],
        ))
    return {"attributed": attributed, "demoted": [k for _, k, _ in spiking]}


@jobs.scheduled("guests.optout_monitor", jobs.every(hours=1), timeout_s=120)
def optout_job(ctx: jobs.JobCtx) -> dict:
    return monitor_optouts(ctx.cur, ctx.hotel_slug)


# ─── Template category and quality (3.10; 1.5 meta_quality) ───


def check_templates(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> dict:
    """Sync every WhatsApp version from Brief; a recategorised step follows
    Meta's category at send time (templates.effective_category); RED quality
    or PAUSED demotes the step's type (meta_quality, once per 7 days)."""
    now = now or clock.now()
    mismatched, demoted = [], []
    for t in templates.list_templates(cur, hotel_slug):
        if t["channel"] != "whatsapp":
            continue
        t = templates.sync_meta(cur, t)
        if t.get("category_meta") and t["category_meta"] != t["category_expected"]:
            mismatched.append(f"{t['meta_name']}/{t['language']}")
        bad = (t.get("quality") or "").upper() == "RED" or (t.get("meta_status") or "").upper() == "PAUSED"
        key = steps.ACTION_TYPES.get(t["step"])
        if bad and key and key not in demoted:
            cur.execute("SELECT 1 FROM action_autonomy_events WHERE hotel_slug = %s AND action_type = %s "
                        "AND trigger = 'meta_quality' AND at >= %s LIMIT 1", (hotel_slug, key, now - timedelta(days=7)))
            if cur.fetchone() is None:
                actions.report_incident(cur, hotel_slug, kind="meta_quality", action_types=[key],
                                        evidence=[{"kind": "row", "label": "template quality", "value": t["meta_name"]}])
                demoted.append(key)
    pending = templates.pending_approval(cur, hotel_slug)
    key = "guests.templates_pending_approval"
    if pending:
        signals.upsert_signal(cur, hotel_slug, signals.SignalIn(
            module="guests", confidence=1.0, kind=key, dedupe_key=key,
            title=f"{pending} journey template versions await your approval",
            summary="Steps without an approved template only observe: nothing is sent until you approve.",
            evidence=[signals.Evidence(kind="metric", label="templates pending", value=pending)],
        ))
    else:
        signals.resolve_signal(cur, hotel_slug, key, resolution="all templates approved")
    return {"mismatched": mismatched, "demoted": demoted, "pending": pending}


@jobs.scheduled("guests.template_category_check", jobs.daily_at("05:10"), timeout_s=120)
def template_job(ctx: jobs.JobCtx) -> dict:
    return check_templates(ctx.cur, ctx.hotel_slug)


# ─── WABA billing readiness (3.5) ───

BILLING_KIND = "guests.wa_billing_not_ready"


def check_billing(cur: Any, hotel_slug: str) -> dict:
    hotel = get_hotel(cur, hotel_slug)
    if not channels.whatsapp_configured(hotel):
        signals.resolve_signal(cur, hotel_slug, BILLING_KIND, resolution="no WhatsApp number")
        return {"configured": False}
    ready = channels.billing_ready(cur, hotel, gsettings.load(cur, hotel_slug))
    if ready:
        signals.resolve_signal(cur, hotel_slug, BILLING_KIND, resolution="payment method on file")
    else:
        signals.upsert_signal(cur, hotel_slug, signals.SignalIn(
            module="guests", confidence=1.0, kind=BILLING_KIND, dedupe_key=BILLING_KIND,
            title="WhatsApp has no confirmed payment method: guest messages go by email",
            summary="Meta reportedly stops delivering service messages for businesses without a payment method "
                    "(from 2026-09-30). Add one in Meta Business Manager, then confirm it in journey settings.",
            recommended_action=signals.RecommendedAction(type="core.task", label="Add a Meta payment method"),
        ))
    return {"configured": True, "ready": ready}


@jobs.scheduled("guests.wa_billing_check", jobs.daily_at("07:05"), timeout_s=60)
def billing_job(ctx: jobs.JobCtx) -> dict:
    return check_billing(ctx.cur, ctx.hotel_slug)
