"""
Owner Brief public API (frozen signatures; other modules import only this file).

Canon 18: daily = money at risk only; weekly = visibility, rank, AI share of
voice, NRevPAR trend, review themes; monthly = P&L pulse. Brief owns
reminders and escalation on actions.
"""

from __future__ import annotations

from typing import Any, Literal

Cadence = Literal["daily", "weekly", "monthly"]


def push_now(cur: Any, hotel_slug: str, *, cadence: Cadence = "daily", user_id: int | None = None) -> dict:
    """Assemble and send a brief immediately (demo button, tests).
    -> {ok, brief_id, sent_to[], reason?}

    Generates a fresh run of that cadence (never replacing the scheduled
    one), mints each recipient a 72 h magic link scoped to the surfaced
    actions, and sends by the first channel that accepts (WhatsApp, then
    email). With `user_id`, only that active member receives it. `sent_to`
    is [{user_id, name, channel}]; `ok` is False with `reason`
    ("no_recipients" | "not_delivered" | "invalid_cadence") when nobody got it."""
    from clefs.modules.brief import brief  # lazy: other modules import this file at load time

    return brief.push_now(cur, hotel_slug, cadence=cadence, user_id=user_id)


# ─── WhatsApp templates (04 spec 3.4, 3.6, 4.6) ───


def wa_template_info(cur: Any, hotel_slug: str, *, name: str, language: str) -> dict | None:
    """Meta's view of one template: {status (APPROVED|PENDING|REJECTED|PAUSED
    |DISABLED), category (UTILITY|MARKETING|AUTHENTICATION), quality (GREEN|
    YELLOW|RED|UNKNOWN)}, or None when unknown.

    None (never registered or synced) means no WhatsApp template counts as
    Meta-approved: journey sends fall through to email and a step is `observe`
    only when no channel has an approved template (core.actions.template_gate);
    inquiry template sends treat None as category unknown, i.e. marketing rules.

    Reads Brief's registry (brf_wa_templates, refreshed daily by
    wa.template_sync): the hotel's own WABA row first, then the platform's.
    The dict also carries category_expected, scope (hotel|platform),
    rejected_reason and last_synced_at."""
    from clefs.modules.brief import templates  # lazy: other modules import this file at load time

    return templates.template_info(cur, hotel_slug, name=name, language=language)


def submit_template(cur: Any, hotel_slug: str, *, name: str, language: str, category: str, components: list[dict]) -> dict:
    """Submit an owner-approved template version to Meta ->
    {name, language, status, meta_template_id}. Raises NotConnected (409
    not_connected) until Brief has the hotel's WhatsApp Business account
    (PUT /hotels/{slug}/brief/wa/account).

    Validates the definition (clefs.notify limits) and, for UTILITY, refuses
    promotional wording (400 promo_words) before anything reaches Meta.
    Outside NOTIFY_MODE=live the row is recorded as PENDING without a call."""
    from clefs.modules.brief import templates

    return templates.submit_template(cur, hotel_slug, name=name, language=language, category=category,
                                     components=components)


def wa_billing_status(cur: Any, hotel_slug: str) -> dict:
    """WhatsApp billing readiness of the hotel's own WABA (04 spec 3.5):
    {ready: bool | None, checked_at: iso | None, source: "account" | "none"}.
    None = unknown (no account connected, or never checked or attested):
    callers treat it as not ready for free-form sends."""
    cur.execute("SELECT billing_ready, billing_checked_at FROM brf_wa_accounts WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    if row is None:
        return {"ready": None, "checked_at": None, "source": "none"}
    at = row["billing_checked_at"]
    return {"ready": row["billing_ready"], "checked_at": at.isoformat() if at else None, "source": "account"}


# ─── Demo seed door ───
# WHY: the UAE demo seed (clefs.seed.demo_uae) is foundation code, which may
# never import a module's internals (tests/foundation/test_module_contracts).
# It loads this api by name and stores runs with brief.generate: exactly the
# run brief.daily / brief.weekly would store, without the delivery those
# jobs also do (no message may leave a seed). Nothing else calls this.


def demo_seed_door() -> Any:
    """generate(cur, hotel, cadence, *, day, now, trigger, scheduled_local,
    created_by) from brief/brief.py."""
    from types import SimpleNamespace

    from clefs.modules.brief import brief

    return SimpleNamespace(generate=brief.generate)
