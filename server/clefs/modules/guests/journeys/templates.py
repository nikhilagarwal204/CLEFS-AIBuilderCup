"""
The template library (04 spec 3.6): seeded per hotel, EN and AR, WhatsApp
and email, pre-sorted by the Meta category we expect, variables `{{n}}`.

A template may send only when (3.4 template gate):
- the owner approved that version (approved_at), and
- for WhatsApp, Meta reports it APPROVED (brief.api.wa_template_info).

Lint runs on every seeded or edited body and again on the variable values
at send time, so a promotional word can never ride into a utility message
through a KB fact, and a review ask can never ride into J3 or J8.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clefs.core import clock
from clefs.db import Json
from clefs.modules.brief import api as brief_api
from clefs.modules.guests.journeys import steps

log = logging.getLogger("clefs.guests.journeys.templates")

VERSION = 1
LANGUAGES = ("en", "ar")
CHANNELS = ("whatsapp", "email")

# ─── Library ───
# Each variable: (source, max chars). The sender fills values by source
# (executor.variables), so the order here IS the {{n}} order.

LIBRARY: dict[str, dict[str, Any]] = {
    "j1": {
        "name": "gst_j1_confirm_v1",
        "vars": [("first_name", 60), ("hotel_name", 60), ("check_in", 30), ("check_out", 30), ("booking_ref", 40)],
        "en": "Hello {{1}}, your stay at {{2}} is confirmed: {{3}} to {{4}}, booking {{5}}. "
              "To help us prepare, share your arrival time and any requests.",
        "ar": "مرحباً {{1}}، تم تأكيد إقامتك في {{2}} من {{3}} إلى {{4}}، رقم الحجز {{5}}. "
              "لمساعدتنا في التحضير، شاركنا موعد وصولك وأي طلبات.",
        "buttons": [{"type": "URL", "text": {"en": "Add details", "ar": "أضف التفاصيل"}, "path": "/g/{{1}}", "var": "prefs_token"}],
        "subject": {"en": "Your stay at {hotel} is confirmed", "ar": "تم تأكيد إقامتك في {hotel}"},
    },
    "j2": {
        "name": "gst_j2_planning_v1",
        "vars": [("first_name", 60), ("hotel_name", 60), ("check_in", 30), ("planning_info", 200)],
        "en": "Hello {{1}}, your stay at {{2}} starts on {{3}}. For planning: {{4}}. "
              "Reply here if you need anything before you arrive.",
        "ar": "مرحباً {{1}}، تبدأ إقامتك في {{2}} يوم {{3}}. للتخطيط: {{4}}. راسلنا هنا إذا احتجت أي شيء قبل وصولك.",
        "buttons": [],
        "subject": {"en": "Planning your stay at {hotel}", "ar": "التخطيط لإقامتك في {hotel}"},
    },
    "j3": {
        "name": "gst_j3_offer_v1",
        "vars": [("first_name", 60), ("hotel_name", 60), ("check_in", 30), ("offer_title", 80), ("offer_price", 30)],
        "en": "Hello {{1}}, this is {{2}}. Before you arrive on {{3}}: {{4}} for {{5}}. "
              "Reply YES to add it to your booking. Reply STOP to opt out of offers.",
        "ar": "مرحباً {{1}}، معك {{2}}. قبل وصولك يوم {{3}}: {{4}} مقابل {{5}}. "
              "أرسل نعم لإضافته إلى حجزك. أرسل إيقاف لإلغاء الاشتراك في العروض.",
        "buttons": [],
        "subject": {"en": "Before you arrive at {hotel}", "ar": "قبل وصولك إلى {hotel}"},
    },
    "j4": {
        "name": "gst_j4_checkin_v1",
        "vars": [("first_name", 60), ("check_in", 30), ("checkin_time", 10), ("checkin_bring", 200), ("directions", 200)],
        "en": "Hello {{1}}, we look forward to welcoming you on {{2}}. Check-in is from {{3}}. Please bring {{4}}. "
              "Directions: {{5}}. Reply here if you need help finding us.",
        "ar": "مرحباً {{1}}، نتطلع إلى استقبالك يوم {{2}}. تسجيل الوصول من الساعة {{3}}. يرجى إحضار {{4}}. "
              "الاتجاهات: {{5}}. راسلنا هنا إذا احتجت مساعدة في الوصول إلينا.",
        "buttons": [],
        "subject": {"en": "Check-in at {hotel}", "ar": "تسجيل الوصول في {hotel}"},
    },
    "j5": {
        "name": "gst_j5_daycheck_v1",
        "vars": [("first_name", 60), ("hotel_name", 60), ("room", 30), ("check_out", 30)],
        "en": "Hello {{1}}, this is {{2}}. How is room {{3}} for your stay until {{4}}? "
              "If anything needs fixing, reply here and we will send someone.",
        "ar": "مرحباً {{1}}، معك {{2}}. كيف الغرفة {{3}} خلال إقامتك حتى {{4}}؟ "
              "إذا كان هناك ما يحتاج إلى إصلاح، راسلنا هنا وسنرسل من يساعدك.",
        "buttons": [
            {"type": "QUICK_REPLY", "text": {"en": "All good", "ar": "كل شيء جيد"}, "payload": "gst_j5_ok"},
            {"type": "QUICK_REPLY", "text": {"en": "Something to fix", "ar": "هناك مشكلة"}, "payload": "gst_j5_fix"},
        ],
        "subject": {"en": "How is your stay at {hotel}?", "ar": "كيف إقامتك في {hotel}؟"},
    },
    "j7": {
        "name": "gst_j7_feedback_v1",
        "vars": [("first_name", 60), ("hotel_name", 60), ("check_in", 30), ("check_out", 30)],
        "en": "Hello {{1}}, thank you for staying at {{2}} from {{3}} to {{4}}. How was your stay? It takes one minute.",
        "ar": "مرحباً {{1}}، شكراً لإقامتك في {{2}} من {{3}} إلى {{4}}. كيف كانت إقامتك؟ يستغرق ذلك دقيقة واحدة.",
        "buttons": [{"type": "URL", "text": {"en": "Give feedback", "ar": "شاركنا رأيك"}, "path": "/g/f/{{1}}", "var": "feedback_token"}],
        "subject": {"en": "Thank you for staying at {hotel}", "ar": "شكراً لإقامتك في {hotel}"},
    },
    "j8": {
        "name": "gst_j8_rebook_v1",
        "vars": [("first_name", 60), ("hotel_name", 60), ("direct_code", 20), ("offer_title", 80), ("valid_to", 30),
                 ("booking_url", 300)],
        "en": "Hello {{1}}, thank you again for staying at {{2}}. Book direct with code {{3}} for {{4}} until {{5}}: {{6}}. "
              "Reply STOP to opt out.",
        "ar": "مرحباً {{1}}، شكراً مجدداً لإقامتك في {{2}}. احجز مباشرة بالرمز {{3}} للحصول على {{4}} حتى {{5}}: {{6}}. "
              "أرسل إيقاف لإلغاء الاشتراك.",
        "buttons": [],
        "subject": {"en": "Welcome back to {hotel}", "ar": "مرحباً بعودتك إلى {hotel}"},
    },
}

# ─── Lint (3.6; uae/whatsapp.md section 2a) ───

EM_DASH = "\u2014"  # escaped: the repo bans the literal character everywhere
MAX_BODY = 1024
MAX_QUICK_REPLY = 25
MAX_URL_BUTTONS = 2
MAX_HEADER = 60
VAR_RE = re.compile(r"\{\{(\d+)\}\}")
# Utility bodies and values must not promote (Meta recategorises them).
PROMO_WORDS = ("offer", "deal", "discount", "% off", "promo", "save", "special", "free", "upgrade",
               "عرض", "خصم", "تخفيض", "مجاني", "ترقية")
# J3 and J8 never ask for reviews (3.5 review policy).
REVIEW_WORDS = ("review", "rate us", "rating", "تقييم", "مراجعة")
STOP_TEXT = ("stop", "إيقاف")


def _has_word(text: str, words: tuple[str, ...]) -> list[str]:
    low = (text or "").lower()
    hits = []
    for w in words:
        if w.isascii() and w[0].isalpha():
            if re.search(rf"\b{re.escape(w)}", low):
                hits.append(w)
        elif w in low:
            hits.append(w)
    return hits


def lint(body: str, *, category: str, step: str, variables: list[dict] | None = None,
         buttons: list[dict] | None = None, header: str | None = None, footer: str | None = None) -> dict:
    """{ok, errors: [code...]} for one template version. Codes are stable
    strings the console shows next to the template."""
    errors: list[str] = []
    body = body or ""
    nums = [int(n) for n in VAR_RE.findall(body)]
    declared = len(variables or [])
    if EM_DASH in body or any(EM_DASH in (x or "") for x in (header, footer)):
        errors.append("em_dash")
    if sorted(set(nums)) != list(range(1, declared + 1)):
        errors.append("variable_count")
    if len(body) > MAX_BODY:
        errors.append("body_too_long")
    stripped = body.strip().rstrip(".!?؟。 ")
    if VAR_RE.match(body.strip()) or re.search(r"\{\{\d+\}\}$", stripped):
        errors.append("variable_at_edge")
    if re.search(r"\}\}\s*\{\{", body):
        errors.append("adjacent_variables")
    if any(len(x or "") > MAX_HEADER for x in (header, footer)):
        errors.append("header_footer_too_long")
    urls = [b for b in (buttons or []) if b.get("type") == "URL"]
    if len(urls) > MAX_URL_BUTTONS:
        errors.append("too_many_url_buttons")
    for b in urls:
        path = b.get("path") or ""
        if VAR_RE.search(path) and not re.search(r"\{\{\d+\}\}$", path):
            errors.append("url_variable_not_suffix")
    for b in buttons or []:
        if b.get("type") == "QUICK_REPLY":
            texts = b.get("text") if isinstance(b.get("text"), dict) else {"x": b.get("text")}
            if any(len(t or "") > MAX_QUICK_REPLY for t in texts.values()):
                errors.append("quick_reply_too_long")
    if category == "UTILITY" and _has_word(body, PROMO_WORDS):
        errors.append("utility_promotional")
    if category == "MARKETING":
        if not any(v.get("source") == "hotel_name" for v in (variables or [])):
            errors.append("marketing_no_hotel_name")
        if not _has_word(body, STOP_TEXT):
            errors.append("marketing_no_stop")
    if step in steps.MARKETING_STEPS and _has_word(body, REVIEW_WORDS):
        errors.append("review_words")
    return {"ok": not errors, "errors": sorted(set(errors))}


def lint_values(values: list[str], *, category: str, step: str) -> list[str]:
    """Send-time lint of the filled variables (3.6: utility lint covers the
    values too, so a KB fact cannot smuggle an offer into J2)."""
    errors: list[str] = []
    for v in values:
        s = str(v or "")
        if EM_DASH in s:
            errors.append("em_dash")
        if category == "UTILITY" and _has_word(s, PROMO_WORDS):
            errors.append("utility_promotional")
        if step in steps.MARKETING_STEPS and _has_word(s, REVIEW_WORDS):
            errors.append("review_words")
    return sorted(set(errors))


def variables_of(step: str) -> list[dict]:
    return [{"n": i + 1, "source": src, "max": mx} for i, (src, mx) in enumerate(LIBRARY[step]["vars"])]


def buttons_of(step: str, language: str) -> list[dict]:
    out = []
    for b in LIBRARY[step]["buttons"]:
        out.append({**{k: v for k, v in b.items() if k != "text"}, "text": b["text"][language]})
    return out


# ─── Seeding ───

TEMPLATE_COLS = (
    "id, hotel_slug, step, channel, meta_name, version, language, category_expected, category_meta, meta_status, "
    "quality, subject, body, buttons, variables, lint, approved_by, approved_at, submitted_at, synced_at, active, created_at"
)


def ensure_library(cur: Any, hotel_slug: str) -> int:
    """Seed version 1 of every step x channel x language for the hotel.
    Idempotent (UNIQUE hotel, step, channel, language, version); nothing is
    approved: the owner approves each version in the console."""
    n = 0
    for step, spec in LIBRARY.items():
        cat = steps.EXPECTED_CATEGORY[step]
        variables = variables_of(step)
        for lang in LANGUAGES:
            buttons = buttons_of(step, lang)
            result = lint(spec[lang], category=cat, step=step, variables=variables, buttons=buttons)
            for ch in CHANNELS:
                cur.execute(
                    """
                    INSERT INTO gst_journey_templates (hotel_slug, step, channel, meta_name, version, language,
                        category_expected, subject, body, buttons, variables, lint, active, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                    ON CONFLICT (hotel_slug, step, channel, language, version) DO NOTHING
                    """,
                    (hotel_slug, step, ch, spec["name"], VERSION, lang, cat,
                     spec["subject"][lang] if ch == "email" else None, spec[lang], Json(buttons), Json(variables),
                     Json(result), clock.now()),
                )
                n += cur.rowcount
    return n


def list_templates(cur: Any, hotel_slug: str) -> list[dict]:
    ensure_library(cur, hotel_slug)
    cur.execute(
        f"SELECT {TEMPLATE_COLS} FROM gst_journey_templates WHERE hotel_slug = %s AND active "
        "ORDER BY step, channel, language, version DESC",
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_template(cur: Any, hotel_slug: str, template_id: int) -> dict | None:
    cur.execute(f"SELECT {TEMPLATE_COLS} FROM gst_journey_templates WHERE hotel_slug = %s AND id = %s",
                (hotel_slug, template_id))
    row = cur.fetchone()
    return dict(row) if row else None


def template_ref(t: dict) -> str:
    """ExecResult.template_id: meta_name@version."""
    return f"{t.get('meta_name') or 'gst_' + t['step']}@{t['version']}"


# ─── Meta status (brief.api.wa_template_info) ───


def sync_meta(cur: Any, t: dict) -> dict:
    """Refresh a WhatsApp template's Meta status and category from Brief.
    None from Brief (the stub, or a template Meta does not know) keeps what
    we had: a template is Meta-approved only when Brief said so."""
    if t["channel"] != "whatsapp":
        return t
    try:
        info = brief_api.wa_template_info(cur, t["hotel_slug"], name=t["meta_name"], language=t["language"])
    except Exception:  # Brief's integration failing must not stop a send decision
        log.exception("wa_template_info failed for %s", t["meta_name"])
        info = None
    if not info:
        return t
    changes = {
        "meta_status": (info.get("status") or t.get("meta_status")),
        "category_meta": (info.get("category") or t.get("category_meta")),
        "quality": (info.get("quality") or t.get("quality")),
    }
    cur.execute(
        "UPDATE gst_journey_templates SET meta_status = %s, category_meta = %s, quality = %s, synced_at = %s WHERE id = %s",
        (changes["meta_status"], changes["category_meta"], changes["quality"], clock.now(), t["id"]),
    )
    return {**t, **changes}


def is_sendable(t: dict | None) -> bool:
    """Owner approved, lint clean, and for WhatsApp Meta APPROVED."""
    if not t or not t.get("active") or not t.get("approved_at"):
        return False
    if not (t.get("lint") or {}).get("ok", False):
        return False
    return t["channel"] == "email" or (t.get("meta_status") or "").upper() == "APPROVED"


def effective_category(t: dict) -> str:
    """What Meta says wins: a utility step Meta recategorised to MARKETING
    follows the marketing rules (3.5 template gate)."""
    meta = (t.get("category_meta") or "").upper()
    return meta if meta in ("UTILITY", "MARKETING") else t["category_expected"]


def approved_template(cur: Any, hotel_slug: str, step: str, channel: str, language: str | None,
                      *, sync: bool = True) -> dict | None:
    """The newest sendable version for the guest's language, else EN."""
    ensure_library(cur, hotel_slug)
    langs = [language, "en"] if language in ("ar",) else ["en"]
    for lang in langs:
        cur.execute(
            f"SELECT {TEMPLATE_COLS} FROM gst_journey_templates WHERE hotel_slug = %s AND step = %s AND channel = %s "
            "AND language = %s AND active AND approved_at IS NOT NULL ORDER BY version DESC",
            (hotel_slug, step, channel, lang),
        )
        for row in [dict(r) for r in cur.fetchall()]:
            t = sync_meta(cur, row) if sync else row
            if is_sendable(t):
                return t
    return None


# ─── Owner approval and Meta submission ───


def _components(t: dict) -> list[dict]:
    """Meta's component list for a WhatsApp template version: the body with
    one example per variable, and its buttons."""
    examples = [f"sample{v['n']}" for v in t.get("variables") or []]
    comps: list[dict] = [{"type": "BODY", "text": t["body"], "example": {"body_text": [examples]} if examples else {}}]
    btns = []
    for b in t.get("buttons") or []:
        if b.get("type") == "URL":
            btns.append({"type": "URL", "text": b["text"], "url": "https://{web}" + b.get("path", "")})
        elif b.get("type") == "QUICK_REPLY":
            btns.append({"type": "QUICK_REPLY", "text": b["text"]})
    if btns:
        comps.append({"type": "BUTTONS", "buttons": btns})
    return comps


def approve(cur: Any, hotel_slug: str, template_id: int, *, actor_user_id: int | None) -> dict:
    """The owner approves one version. Lint must be clean. A WhatsApp
    version is then submitted to Meta through Brief; until Brief has the
    hotel's WABA (NotConnected) it stays owner-approved and unsent on
    WhatsApp, and email carries the step."""
    from clefs.core.errors import ClefsError

    t = get_template(cur, hotel_slug, template_id)
    if t is None:
        raise ClefsError(404, "Template not found", "template_not_found")
    result = lint(t["body"], category=t["category_expected"], step=t["step"], variables=t["variables"],
                  buttons=t["buttons"], header=t.get("subject") if t["channel"] == "whatsapp" else None)
    if not result["ok"]:
        raise ClefsError(422, f"Template fails lint: {', '.join(result['errors'])}", "template_lint_failed")
    now = clock.now()
    submitted_at = t.get("submitted_at")
    submission: dict | None = None
    if t["channel"] == "whatsapp" and submitted_at is None:
        try:
            submission = brief_api.submit_template(
                cur, hotel_slug, name=t["meta_name"], language=t["language"], category=t["category_expected"],
                components=_components(t),
            )
            submitted_at = now
        except Exception as exc:  # NotConnected until Brief connects the WABA
            submission = {"error": getattr(exc, "code", type(exc).__name__)}
    meta_status = (submission or {}).get("status") or t.get("meta_status")
    cur.execute(
        """
        UPDATE gst_journey_templates SET approved_at = %s, approved_by = %s, lint = %s, submitted_at = %s,
            meta_status = %s
        WHERE id = %s RETURNING *
        """,
        (now, actor_user_id, Json(result), submitted_at, meta_status, template_id),
    )
    row = dict(cur.fetchone())
    row["submission"] = submission
    return row


def revoke(cur: Any, hotel_slug: str, template_id: int) -> dict | None:
    """Withdraw the owner's approval: the step drops to observe (or the
    other channel) at the next dispatch and at execution (the gate)."""
    cur.execute(
        "UPDATE gst_journey_templates SET approved_at = NULL, approved_by = NULL WHERE hotel_slug = %s AND id = %s RETURNING *",
        (hotel_slug, template_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def pending_approval(cur: Any, hotel_slug: str) -> int:
    """Seeded versions the owner has not approved (the weekly signal)."""
    cur.execute(
        "SELECT COUNT(*) AS n FROM gst_journey_templates WHERE hotel_slug = %s AND active AND approved_at IS NULL",
        (hotel_slug,),
    )
    return int(cur.fetchone()["n"])
