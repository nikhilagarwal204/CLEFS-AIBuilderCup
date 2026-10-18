"""
Inquiry Guard jobs, signal kinds and PII registrations (04 spec 4.1, 4.11).

Imported by module.py, so registration happens when the manifest loads (the
app and the worker's jobs.load_all both import it).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core.privacy import register_pii
from clefs.core.signals import register_signal_kind
from clefs.modules.inquiries import extraction, sla

# ─── Signal kinds (4.11; the intake side) ───

register_signal_kind(
    "inq.unanswered", label="Unanswered inquiry past its SLA", default_owner_role="front_desk",
    section="money_available", cadence="daily", icon="inbox",
)
register_signal_kind(
    "inq.lead_fetch_failed", label="Ad lead could not be fetched", default_owner_role="gm",
    section="service_risk", cadence="weekly", icon="alert",
)

# ─── Retention (4.1) ───
# Spec 4.1 asks for two registrations on inq_inquiries (730 d open, 365 d
# spam/lost). The registry keeps one spec per (module, table), so the 730 d
# rule is registered here and the 365 d rule runs in inquiries.purge below.
# FOUNDATION-REQUEST: let register_pii take several specs per table (keyed
# by `where`), so Trust's retention job can own both rules.
CONTACT_COLUMNS = ("contact_name", "contact_phone_enc", "contact_phone_hmac", "contact_email_enc", "contact_email_hmac")
register_pii(
    "inquiries", "inq_inquiries",
    subject_columns={"contact_name": "name", "contact_phone_hmac": "hmac", "contact_email_hmac": "hmac"},
    ts_column="last_activity_at", retention_days=730, strategy="redact", redact_columns=CONTACT_COLUMNS,
    where="stage NOT IN ('spam', 'lost')",
)
register_pii(
    "inquiries", "inq_messages", subject_columns={"body": "free_text"}, ts_column="created_at",
    retention_days=365, strategy="redact", redact_columns=("body",),
)
# Payment payloads (provider bodies can carry a payer's name or card hint)
# are redacted at 365 d; amounts stay. inq_payment_events.payload is NOT
# NULL, so it is emptied to {} by inquiries.purge instead of the registry.
register_pii(
    "inquiries", "inq_payment_links", subject_columns={"provider_payload": "free_text"}, ts_column="created_at",
    retention_days=365, strategy="redact", redact_columns=("provider_payload",),
)


# ─── Tasks ───


@jobs.task("inquiries.extract", max_attempts=3, backoff_s=60)
def extract_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    return extraction.run(ctx.cur, ctx.hotel, int(payload["inquiry_id"]))


@jobs.task(sla.SLA_TASK, max_attempts=5, backoff_s=30)
def sla_check_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    return sla.check(ctx.cur, ctx.hotel, int(payload["inquiry_id"]), payload.get("due_epoch"))


# ─── Purge: the 365 d spam/lost contact rule ───


@jobs.scheduled("inquiries.purge", jobs.daily_at("03:45"))
def purge(ctx: jobs.JobCtx) -> dict:
    return {"redacted": purge_closed_contacts(ctx.cur, ctx.hotel_slug),
            "payment_events": purge_payment_events(ctx.cur, ctx.hotel_slug)}


def purge_payment_events(cur: Any, hotel_slug: str) -> int:
    """4.1 retention: verified webhook payloads emptied at 365 d (the row,
    its ids and the processing time stay for idempotency and audit)."""
    cur.execute(
        "UPDATE inq_payment_events SET payload = '{}'::jsonb WHERE hotel_slug = %s AND received_at < %s "
        "AND payload <> '{}'::jsonb",
        (hotel_slug, clock.now() - timedelta(days=365)),
    )
    return cur.rowcount


def purge_closed_contacts(cur: Any, hotel_slug: str) -> int:
    cutoff = clock.now() - timedelta(days=365)
    sets = ", ".join(f"{c} = NULL" for c in CONTACT_COLUMNS)
    cur.execute(
        f"""
        UPDATE inq_inquiries SET {sets}, updated_at = %s
        WHERE hotel_slug = %s AND stage IN ('spam', 'lost') AND last_activity_at < %s
          AND (contact_name IS NOT NULL OR contact_phone_enc IS NOT NULL OR contact_email_enc IS NOT NULL
               OR contact_phone_hmac IS NOT NULL OR contact_email_hmac IS NOT NULL)
        """,
        (clock.now(), hotel_slug, cutoff),
    )
    return cur.rowcount


@jobs.task("inquiries.meta_lead_fetch", max_attempts=8, backoff_s=120)
def meta_lead_fetch_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    from clefs.modules.inquiries.intake.leads import fetch_meta_lead

    return fetch_meta_lead(ctx.cur, ctx.hotel, payload)


# ─── Future reminders (4.2: future --remind date--> new) ───


@jobs.scheduled("inquiries.future_remind", jobs.daily_at("09:30"))
def future_remind(ctx: jobs.JobCtx) -> dict:
    return {"reopened": remind_due(ctx.cur, ctx.hotel)}


def remind_due(cur: Any, hotel: Any) -> int:
    """Move `future` inquiries whose remind date has come back to `new`, with
    a fresh SLA clock: the hotel owes the guest the follow-up it promised."""
    from clefs.core.time import local_date
    from clefs.modules.inquiries import service

    cur.execute(
        "SELECT * FROM inq_inquiries WHERE hotel_slug = %s AND stage = 'future' AND future_remind_on <= %s "
        "ORDER BY id FOR UPDATE SKIP LOCKED",
        (hotel.slug, local_date(hotel)),
    )
    n = 0
    for got in cur.fetchall():
        row = dict(got)
        service.transition(cur, row, "new", reason="future_remind", hotel=hotel)
        sla.arm(cur, hotel, row)
        n += 1
    return n


# ─── Quote-to-cash side (04 spec 4.6-4.11) ───
# Importing these registers the action types, signal kinds and metrics.

from clefs.modules.inquiries import actions as _actions  # noqa: E402,F401  (action types)
from clefs.modules.inquiries import leak, quotes, signals, templates, verify  # noqa: E402
from clefs.modules.inquiries.payments import service as payments  # noqa: E402


@jobs.task(payments.PROCESS_TASK, max_attempts=6, backoff_s=60)
def payment_event_process(ctx: jobs.JobCtx, payload: dict) -> dict:
    return payments.process_event(ctx.cur, ctx.hotel, int(payload["event_id"]))


@jobs.scheduled("inquiries.payment_reconcile", jobs.every(minutes=60))
def payment_reconcile(ctx: jobs.JobCtx) -> dict:
    return payments.reconcile(ctx.cur, ctx.hotel)


@jobs.scheduled("inquiries.verify", jobs.every(minutes=60))
def verify_all(ctx: jobs.JobCtx) -> dict:
    return verify.run(ctx.cur, ctx.hotel)


@jobs.task(verify.VERIFY_TASK, max_attempts=5, backoff_s=60)
def verify_one(ctx: jobs.JobCtx, payload: dict) -> dict:
    return verify.verify_inquiry(ctx.cur, ctx.hotel, int(payload["inquiry_id"]))


@jobs.scheduled("inquiries.leak_match", jobs.daily_at("05:00"))
def leak_match(ctx: jobs.JobCtx) -> dict:
    return leak.run(ctx.cur, ctx.hotel)


@jobs.scheduled("inquiries.quote_expiry", jobs.every(minutes=15))
def quote_expiry(ctx: jobs.JobCtx) -> dict:
    return quotes.expire_due(ctx.cur, ctx.hotel)


@jobs.scheduled("inquiries.pipeline_weekly", jobs.weekly_at(1, "06:30"))
def pipeline_weekly(ctx: jobs.JobCtx) -> dict:
    sig = signals.pipeline_weekly(ctx.cur, ctx.hotel)
    return {"signal_id": sig.get("id")}


@jobs.scheduled("inquiries.recalibrate", jobs.monthly_at(1, "04:00"))
def recalibrate(ctx: jobs.JobCtx) -> dict:
    return signals.recalibrate(ctx.cur, ctx.hotel_slug)


@jobs.scheduled("inquiries.template_seed", jobs.daily_at("03:50"))
def template_seed(ctx: jobs.JobCtx) -> dict:
    """Every hotel has both templates in EN and AR to approve (4.6)."""
    return {"seeded": templates.seed(ctx.cur, ctx.hotel_slug)}


# ─── Hooks: stay events and outbound statuses ───

from clefs import notify as _notify  # noqa: E402
from clefs.core.stays import on_stay_event as _on_stay_event  # noqa: E402

_on_stay_event("inquiries.verify_on_stay")(verify.on_stay_event)
_notify.on_outbound_status("inquiries.quote_ready")(templates.on_template_status)
_notify.on_outbound_status("inquiries.follow_up")(templates.on_template_status)
