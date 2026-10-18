"""
Inquiry WhatsApp templates and the category gate (04 spec 4.6 step 7, 4.10).

Two templates live in inq_templates, seeded per hotel in EN and AR:

- inq_quote_ready_v1: the quote link outside the 24 h window.
- inq_quote_followup_v1: the "still available" nudge (4.10).

Both are filed as UTILITY but budgeted as marketing: Meta files mixed content
as MARKETING by default (research uae/whatsapp.md section 3). A template send
therefore asks Brief what Meta actually filed (`brief.api.wa_template_info`)
and, when that is MARKETING or unknown (the None stub included), applies the
3.5 marketing rules: only inside [09:00, 18:00) Asia/Dubai, never to a +1
number, and on 131049 one retry after 24 h, then email.

The follow-up template reads like a reminder, so it is ALWAYS timed inside the
window whatever its category (4.10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from clefs.core import clock
from clefs.core.errors import not_found

QUOTE_READY = "inq_quote_ready_v1"
FOLLOWUP = "inq_quote_followup_v1"
TEMPLATE_NAMES = (QUOTE_READY, FOLLOWUP)
LANGUAGES = ("en", "ar")

# Marketing hours are Dubai time for every hotel (Canon, UAE defaults):
# Meta's frequency caps and the UAE rules key on the recipient's market.
MARKETING_TZ = ZoneInfo("Asia/Dubai")
WINDOW_OPEN = time(9, 0)
WINDOW_CLOSE = time(18, 0)
RETRY_131049 = timedelta(hours=24)

# ─── Bodies (4.6, 4.10; variables {{n}} as Meta numbers them) ───

BODIES: dict[tuple[str, str], str] = {
    (QUOTE_READY, "en"): "Your quote {{1}} for {{2}} is ready: total {{3}}, valid until {{4}}. "
                         "Reply here with any questions.",
    (QUOTE_READY, "ar"): "عرض السعر {{1}} لـ {{2}} جاهز: الإجمالي {{3}}، صالح حتى {{4}}. راسلنا هنا لأي استفسار.",
    (FOLLOWUP, "en"): "Your quote {{1}} for {{2}} is still available until {{3}}. "
                      "Reply here with any questions or to confirm.",
    (FOLLOWUP, "ar"): "عرض السعر {{1}} لـ {{2}} لا يزال متاحاً حتى {{3}}. راسلنا هنا لأي استفسار أو للتأكيد.",
}
# A URL button whose suffix is the public quote token: /q/{{1}}.
BUTTONS: dict[str, list[dict]] = {
    QUOTE_READY: [{"type": "URL", "text": "View quote", "url": "https://www.clefs.world/q/{{1}}"}],
    FOLLOWUP: [{"type": "URL", "text": "View quote", "url": "https://www.clefs.world/q/{{1}}"}],
}
BUTTON_TEXT_AR = "عرض السعر"


def render(name: str, language: str, params: list[str]) -> str:
    """The body with its {{n}} filled: what the thread and the email show."""
    body = BODIES[(name, "ar" if language == "ar" else "en")]
    for i, value in enumerate(params, start=1):
        body = body.replace("{{%d}}" % i, str(value))
    return body


# ─── Rows ───


def seed(cur: Any, hotel_slug: str) -> int:
    """Insert version 1 of every template and language the hotel lacks.
    Idempotent; never touches a version the owner already approved."""
    n = 0
    for (name, lang), body in BODIES.items():
        buttons = [dict(b, text=BUTTON_TEXT_AR) if lang == "ar" else dict(b) for b in BUTTONS[name]]
        cur.execute(
            """
            INSERT INTO inq_templates (hotel_slug, name, language, body, buttons, version)
            SELECT %s, %s, %s, %s, %s::jsonb, 1
            WHERE NOT EXISTS (SELECT 1 FROM inq_templates WHERE hotel_slug = %s AND name = %s AND language = %s)
            """,
            (hotel_slug, name, lang, body, _json(buttons), hotel_slug, name, lang),
        )
        n += cur.rowcount
    return n


def _json(v: Any) -> str:
    import json

    return json.dumps(v, ensure_ascii=False)


def list_templates(cur: Any, hotel_slug: str) -> list[dict]:
    seed(cur, hotel_slug)
    cur.execute(
        """
        SELECT DISTINCT ON (name, language) * FROM inq_templates
        WHERE hotel_slug = %s ORDER BY name, language, version DESC
        """,
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def latest(cur: Any, hotel_slug: str, name: str, language: str) -> dict | None:
    cur.execute(
        "SELECT * FROM inq_templates WHERE hotel_slug = %s AND name = %s AND language = %s ORDER BY version DESC LIMIT 1",
        (hotel_slug, name, "ar" if language == "ar" else "en"),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Owner approval and Meta submission (4.6 "Template storage and approval") ───


def approve(cur: Any, hotel_slug: str, template_id: int, *, actor: Any) -> dict:
    """The owner approves a template version, which then goes to Meta through
    Brief. Without a connected WhatsApp Business account the approval stands
    and meta_status says `not_connected`: quotes fall back to email."""
    from clefs.audit import audit
    from clefs.core.oauth import NotConnected
    from clefs.modules.brief import api as brief_api

    cur.execute("SELECT * FROM inq_templates WHERE hotel_slug = %s AND id = %s FOR UPDATE", (hotel_slug, template_id))
    row = cur.fetchone()
    if row is None:
        raise not_found("template not found")
    row = dict(row)
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    now = clock.now()
    meta_status = row.get("meta_status")
    components = [{"type": "BODY", "text": row["body"]}]
    if row.get("buttons"):
        components.append({"type": "BUTTONS", "buttons": row["buttons"]})
    try:
        from clefs.modules.inquiries.quotes import guarded

        got = guarded(cur, lambda: brief_api.submit_template(cur, hotel_slug, name=row["name"], language=row["language"],
                                                             category="UTILITY", components=components))
        meta_status = (got or {}).get("status") or ("PENDING" if got else "submit_failed")
    except NotConnected:
        meta_status = "not_connected"
    cur.execute(
        """
        UPDATE inq_templates SET approved_by = %s, approved_at = %s, submitted_at = %s, meta_status = %s
        WHERE id = %s RETURNING *
        """,
        (user_id, now, now if meta_status not in ("not_connected", "submit_failed") else None, meta_status,
         template_id),
    )
    out = dict(cur.fetchone())
    audit(cur, actor, "inquiries.template_approve", "inq_template", template_id,
          after={"name": row["name"], "language": row["language"], "meta_status": meta_status}, hotel_slug=hotel_slug)
    return out


def meta_info(cur: Any, hotel_slug: str, name: str, language: str) -> dict | None:
    """Brief's live view of the template, mirrored onto the row (status and
    category) so the settings page shows what Meta filed."""
    from clefs.modules.brief import api as brief_api

    from clefs.modules.inquiries.quotes import guarded

    info = guarded(cur, lambda: brief_api.wa_template_info(cur, hotel_slug, name=name, language=language))
    if info:
        cur.execute(
            """
            UPDATE inq_templates SET meta_status = %s, category_meta = %s, synced_at = %s
            WHERE id = (SELECT id FROM inq_templates WHERE hotel_slug = %s AND name = %s AND language = %s
                        ORDER BY version DESC LIMIT 1)
            """,
            (info.get("status"), info.get("category"), clock.now(), hotel_slug, name, language),
        )
    return info


def usable(cur: Any, hotel_slug: str, name: str, language: str) -> tuple[bool, dict | None]:
    """(owner-approved AND Meta APPROVED, meta info). Both are needed before
    a template send (4.6); either missing means email."""
    row = latest(cur, hotel_slug, name, language)
    info = meta_info(cur, hotel_slug, name, "ar" if language == "ar" else "en")
    ok = bool(row and row.get("approved_at") and info and info.get("status") == "APPROVED")
    return ok, info


# ─── Category gate (4.6 step 7) ───


@dataclass(frozen=True)
class SendPlan:
    """How a template message goes out: `channel` whatsapp or email (None =
    nowhere: no approved template and no email), `send_at` None = now, and
    `category` what the gate assumed (MARKETING when Meta says so or is
    silent)."""

    channel: str | None
    send_at: datetime | None
    category: str
    reason: str | None = None


def in_marketing_window(at: datetime) -> bool:
    local = at.astimezone(MARKETING_TZ).time()
    return WINDOW_OPEN <= local < WINDOW_CLOSE


def next_window_start(at: datetime) -> datetime:
    """The next 09:00 Dubai at or after `at` (`at` itself when inside)."""
    if in_marketing_window(at):
        return at
    local = at.astimezone(MARKETING_TZ)
    day = local.date() if local.time() < WINDOW_OPEN else local.date() + timedelta(days=1)
    return datetime.combine(day, WINDOW_OPEN, tzinfo=MARKETING_TZ)


def is_nanp(phone: str | None) -> bool:
    return bool(phone) and str(phone).replace(" ", "").startswith("+1")


def plan_send(
    cur: Any,
    hotel_slug: str,
    *,
    name: str,
    language: str,
    phone: str | None,
    email: str | None,
    at: datetime | None = None,
    deadline: datetime | None = None,
) -> SendPlan:
    """Decide channel and time for one template send (4.6 step 7, 4.10).

    - No owner-approved, Meta-APPROVED template or no phone: email (None
      when there is no email either: the action fails `template_missing`).
    - Category MARKETING or unknown: no WhatsApp to +1 numbers (email), and
      only inside [09:00, 18:00) Dubai; outside it the send moves to the next
      09:00, or goes by email now when that would pass `deadline` (the quote
      would expire before the guest saw it).
    - The follow-up template is always windowed, whatever its category."""
    at = at or clock.now()
    ok, info = usable(cur, hotel_slug, name, language) if phone else (False, None)
    category = str((info or {}).get("category") or "UNKNOWN").upper()
    marketing = category != "UTILITY"
    email_plan = SendPlan("email" if email else None, None, category)
    if not phone or not ok:
        return SendPlan(email_plan.channel, None, category, "template_missing")
    if marketing and is_nanp(phone):
        return SendPlan(email_plan.channel, None, category, "nanp_no_marketing")
    if (marketing or name == FOLLOWUP) and not in_marketing_window(at):
        start = next_window_start(at)
        if deadline is not None and start >= deadline:
            return SendPlan(email_plan.channel, None, category, "window_after_deadline")
        return SendPlan("whatsapp", start, category, "deferred_to_window")
    return SendPlan("whatsapp", None, category, None)


# ─── 131049: one retry after 24 h, then email ───


def on_template_status(cur: Any, row: dict, status: str, error_code: str | None) -> None:
    """Outbox status hook for inquiry template sends. The outbox itself
    re-sends a 131049 failure after 24 h (a retry copy). The first failure
    is left to that retry; when the retry fails with 131049 too, the next
    copy is cancelled and the message goes by email instead."""
    if status != "failed" or error_code != "131049" or row.get("channel") != "whatsapp":
        return
    meta = row.get("meta") or {}
    if int(meta.get("retry") or 0) < 1:
        return  # the outbox's own 24 h retry is the one retry we allow
    nxt = meta.get("retried_by")
    if nxt:
        # Retract the outbox's next queued copy (and its delivery task)
        # through notify's public door; a copy already sent stays sent.
        from clefs import notify

        notify.cancel_queued(cur, outbox_id=int(nxt), reason="inquiries_131049_email_fallback")
    from clefs.modules.inquiries import quotes

    quotes.email_fallback_for_outbox(cur, row, reason="131049")

