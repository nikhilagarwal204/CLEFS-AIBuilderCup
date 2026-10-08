"""
WhatsApp template registry (02 spec 6.2; 04 spec 3.4, 4.6 doors in brief.api).

    template_info(cur, slug, name=..., language=...)   -> {status, category, quality} | None
    submit_template(cur, slug, name=..., ...)          -> {name, language, status, meta_template_id}
    sync(cur, hotel_slug=None)                          -> Meta's view of one WABA, stored
    owner_brief_variables(run, hotel)                   -> header + the 7 body variables

Sending is clefs.notify's (send_whatsapp_template); this file only keeps what
Meta says about each template, so every lane can ask "is it APPROVED, and is
it still UTILITY?" before a business-initiated send (Canon: WhatsApp).

Two scopes: hotel_slug NULL is the platform WABA (settings.WA_BUSINESS_ACCOUNT_ID:
owner_brief_v1, weekly_report_v1, clefs_magic_link), and a hotel row is the
hotel's own WABA (brf_wa_accounts: journey and inquiry templates). A lookup
tries the hotel first, then the platform.

The brief is a UTILITY template with no promotional content: a template
definition with promo words is refused before it reaches Meta, and the
per-send variables go through render.lint. The daily sync alerts once per
change when Meta pauses, rejects or recategorises a template.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Callable

from clefs import db
from clefs.core import clock, money
from clefs.core import signals as core_signals
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel
from clefs.modules.brief import render
from clefs.settings import get_settings

log = logging.getLogger("clefs.brief.templates")

OWNER_BRIEF = "owner_brief_v1"
WEEKLY_REPORT = "weekly_report_v1"
LANG = "en"
BODY_LINES = 5  # {{3}}..{{7}}
HEADER_MAX = 60
ALERT_STATUSES = frozenset({"PAUSED", "REJECTED", "DISABLED", "FLAGGED"})
CATEGORIES = ("UTILITY", "MARKETING", "AUTHENTICATION")


# ─── Definitions (what we submit; Meta's create-template format) ───


def _url_base() -> str:
    return f"{get_settings().web_base_url}/brief/"


def definitions() -> dict[str, dict]:
    """The platform templates Brief owns. The URL button's dynamic suffix is
    the magic-link token, so "Open brief" lands on /brief/{token}."""
    base = _url_base()
    return {
        OWNER_BRIEF: {
            "category": "UTILITY",
            "language": LANG,
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": "Clefs brief · {{1}}", "example": {"header_text": ["2026-10-01"]}},
                {
                    "type": "BODY",
                    "text": "{{1}} decisions pending today, {{2}} at stake.\n\n{{3}}\n{{4}}\n{{5}}\n{{6}}\n{{7}}",
                    "example": {"body_text": [[
                        "3", "AED 4,120", "1) AED 1,020 · Booking no-show commission · 90% · by 14 Oct · Finance",
                        "2) AED 2,400 · VCC not charged · 80% · by 3 Oct · Front office",
                        "3) AED 700 · Minibar variance floor 5 · 60% · Housekeeping",
                        "Verified: AED 3,050 this month", "-",
                    ]]},
                },
                {"type": "FOOTER", "text": "Reply STOP to pause WhatsApp briefs"},
                {"type": "BUTTONS", "buttons": [
                    {"type": "QUICK_REPLY", "text": "Approve #1"},
                    {"type": "QUICK_REPLY", "text": "Review items"},
                    {"type": "URL", "text": "Open brief", "url": base + "{{1}}", "example": [base + "abc123"]},
                ]},
            ],
        },
        WEEKLY_REPORT: {
            "category": "UTILITY",
            "language": LANG,
            "components": [
                {"type": "HEADER", "format": "DOCUMENT", "example": {"header_handle": ["clefs-weekly.pdf"]}},
                {
                    "type": "BODY",
                    "text": "Your weekly results for {{1}}: {{2}} verified, {{3}} identified. The report is attached.",
                    "example": {"body_text": [["2026-W40", "AED 3,050", "AED 9,800"]]},
                },
                {"type": "BUTTONS", "buttons": [
                    {"type": "URL", "text": "Open report", "url": base + "{{1}}", "example": [base + "abc123"]},
                ]},
            ],
        },
    }


# ─── Promo-word lint (definitions are refused; variables are rewritten) ───

_PROMO_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p, _ in render.PROMO_REPLACEMENTS)


def promo_hits(text: str) -> list[str]:
    """Promotional words in a template's fixed text ("offer", "deal", "sale",
    "% off", "promo", ...). A UTILITY template carrying one is recategorised
    MARKETING by Meta, which moves the owner brief onto marketing pricing,
    the 09:00-18:00 send window and the marketing opt-in rules."""
    hits: list[str] = []
    for pat in _PROMO_PATTERNS:
        hits += [m.group(0) for m in pat.finditer(text or "")]
    return hits


def lint_definition(components: list[dict]) -> list[str]:
    texts: list[str] = []
    for c in components or []:
        texts.append(str(c.get("text") or ""))
        for b in c.get("buttons") or []:
            texts.append(str(b.get("text") or ""))
    return [h for t in texts for h in promo_hits(t)]


# ─── Per-send variables ───


def _money(minor: int, ccy: str, locale: str | None) -> str:
    return money.fmt_money(minor or 0, ccy, locale, minor=True).replace(".00", "")


def owner_brief_variables(run: dict, hotel: Hotel) -> dict:
    """owner_brief_v1's header and 7 body variables, within budget:
    {{1}} pending count, {{2}} money at stake, {{3}}..{{7}} item lines, with
    the verified line in the first free slot and "-" after it (Meta refuses
    empty parameters, and parameters cannot carry newlines, so the template
    fixes five line slots). All linted; the body stays <= 1,024 characters."""
    items = list(run.get("items") or []) + list(run.get("overdue") or [])
    # {{2}} keeps the minor digits ("AED 1,020.00"): it is a total, the lines round.
    # "(incl. est.)" when the total sums an estimate (04 spec 4.5, 02 spec 4.3).
    head = [str(len(items)), render.stake_text(run.get("at_stake_minor"), run.get("currency") or hotel.currency,
                                               hotel.locale, estimated=render.any_estimate(run))]
    ver = render.lint(str((run.get("verified") or {}).get("text") or ""))
    fixed = sum(len(x) for x in head) + (len(ver) if ver and len(items) < BODY_LINES else 0)
    lines = render.budget_lines(run, tz=hotel.timezone, locale=hotel.locale, fixed=fixed, slots=BODY_LINES)
    lines = [_flat(x) for x in lines]
    if ver and len(items) < BODY_LINES:
        lines[len(items)] = _flat(ver)
    # The ISO date: unambiguous in every locale the brief goes to.
    return {"header": f"Clefs brief · {run.get('brief_date')}"[:HEADER_MAX], "params": fit(head + lines)}


def fit(params: list[str], limit: int = render.BODY_LIMIT) -> list[str]:
    """Last guard on the 1,024 budget: shorten the longest parameter until
    the total fits (budget_lines already shrinks titles; this covers a long
    verified line or money string on top of maximal lines)."""
    out = list(params)
    while sum(len(x) for x in out) > limit:
        i = max(range(len(out)), key=lambda k: len(out[k]))
        over = sum(len(x) for x in out) - limit
        keep = max(1, len(out[i]) - over - 1)
        out[i] = out[i][:keep].rstrip() + "…" if keep > 1 else "-"
    return out


def weekly_report_variables(report: dict, hotel: Hotel) -> list[str]:
    p = report.get("payload") or {}
    ccy = report.get("currency") or hotel.currency
    return [
        str(report.get("period_key") or "-"),
        _money(int(p.get("verified_minor") or report.get("verified_minor") or 0), ccy, hotel.locale),
        _money(int(p.get("identified_minor") or report.get("identified_minor") or 0), ccy, hotel.locale),
    ]


def _flat(text: str) -> str:
    """Meta refuses parameters with newlines, tabs or 5+ spaces in a row."""
    out = re.sub(r"[\r\n\t]+", " ", text or "")
    out = re.sub(r" {4,}", "   ", out).strip()
    return out or "-"


# ─── Registry ───

_INFO_COLS = "status, category, quality, category_expected, hotel_slug, rejected_reason, last_synced_at"


def template_info(cur: Any, hotel_slug: str | None, *, name: str, language: str) -> dict | None:
    """Meta's view of one template: the hotel's own row first, then the
    platform WABA's. {status, category, quality, category_expected, scope,
    rejected_reason, last_synced_at}, or None when neither knows it."""
    cur.execute(
        f"""
        SELECT {_INFO_COLS} FROM brf_wa_templates
        WHERE name = %s AND language = %s AND (hotel_slug = %s OR hotel_slug IS NULL)
        ORDER BY hotel_slug NULLS LAST LIMIT 1
        """,
        (name, language, hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "status": row["status"],
        "category": row["category"] or row["category_expected"],
        "quality": row["quality"] or "UNKNOWN",
        "category_expected": row["category_expected"],
        "scope": "hotel" if row["hotel_slug"] else "platform",
        "rejected_reason": row["rejected_reason"],
        "last_synced_at": row["last_synced_at"].isoformat() if row["last_synced_at"] else None,
    }


def is_sendable(info: dict | None, *, category: str = "UTILITY") -> bool:
    """APPROVED and still in the category we built it for. A utility
    template Meta moved to MARKETING is not sendable as utility."""
    return bool(info) and info.get("status") == "APPROVED" and (info.get("category") or category) == category


def upsert(cur: Any, hotel_slug: str | None, name: str, language: str, **fields: Any) -> dict:
    """Insert or update one registry row. Unknown keys are refused."""
    allowed = {"category_expected", "category", "status", "quality", "rejected_reason", "components",
               "meta_template_id", "submitted_at", "last_synced_at", "alerted_state"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown template fields: {sorted(bad)}")
    vals = {k: (db.Json(v) if k == "components" else v) for k, v in fields.items()}
    vals.setdefault("category_expected", (fields.get("category") or "UTILITY"))
    cols = ["hotel_slug", "name", "language", *vals.keys(), "updated_at"]
    params = [hotel_slug, name, language, *vals.values(), clock.now()]
    # The expectation is what WE built the template as: a sync (Meta's view)
    # never rewrites it, only an explicit submit does.
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in [*vals.keys(), "updated_at"]
                     if k != "category_expected" or k in fields)
    cur.execute(
        f"""
        INSERT INTO brf_wa_templates ({", ".join(cols)}) VALUES ({", ".join(["%s"] * len(cols))})
        ON CONFLICT ((COALESCE(hotel_slug, '')), name, language) DO UPDATE SET {sets}
        RETURNING *
        """,
        params,
    )
    return dict(cur.fetchone())


def list_templates(cur: Any, hotel_slug: str | None) -> list[dict]:
    """The hotel's rows and the platform rows (the console's template list)."""
    cur.execute(
        """
        SELECT id, hotel_slug, name, language, category_expected, category, status, quality, rejected_reason,
               meta_template_id, submitted_at, last_synced_at, updated_at
        FROM brf_wa_templates WHERE hotel_slug = %s OR hotel_slug IS NULL
        ORDER BY hotel_slug NULLS FIRST, name, language
        """,
        (hotel_slug,),
    )
    out = []
    for r in cur.fetchall():
        row = dict(r)
        row["scope"] = "hotel" if row.pop("hotel_slug") else "platform"
        for k in ("submitted_at", "last_synced_at", "updated_at"):
            row[k] = row[k].isoformat() if row[k] else None
        out.append(row)
    return out


def ensure_platform_templates(cur: Any) -> list[dict]:
    """Register Brief's own definitions as DRAFT rows (idempotent): the sync
    or a submit moves them on. A row already past DRAFT keeps its status."""
    out = []
    for name, d in definitions().items():
        cur.execute(
            """
            INSERT INTO brf_wa_templates (hotel_slug, name, language, category_expected, components)
            VALUES (NULL, %s, %s, %s, %s)
            ON CONFLICT ((COALESCE(hotel_slug, '')), name, language) DO NOTHING
            """,
            (name, d["language"], d["category"], db.Json(d["components"])),
        )
        out.append({"name": name, "language": d["language"]})
    return out


# ─── Graph API (live mode only; tests inject a fake) ───

GraphFn = Callable[[str, str, dict | None, dict | None], dict]
_graph_override: GraphFn | None = None


class NotLive(Exception):
    """NOTIFY_MODE is not live, or no WA_ACCESS_TOKEN: nothing to call."""


def set_graph(fn: GraphFn | None) -> None:
    """Tests: fn(method, path, params, json) -> response body."""
    global _graph_override
    _graph_override = fn


def _graph(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict:
    if _graph_override is not None:
        return _graph_override(method, path, params, json)
    s = get_settings()
    if s.NOTIFY_MODE != "live" or not s.WA_ACCESS_TOKEN:
        raise NotLive()
    import httpx

    try:
        r = httpx.request(
            method, f"https://graph.facebook.com/{s.WA_API_VERSION}/{path.lstrip('/')}", params=params, json=json,
            headers={"Authorization": f"Bearer {s.WA_ACCESS_TOKEN}"}, timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise ClefsError(502, "WhatsApp is not reachable right now", "whatsapp_unreachable") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        raise ClefsError(502, str(err.get("message") or f"Meta answered {r.status_code}")[:300], "whatsapp_error")
    return body


def waba_for(cur: Any, hotel_slug: str | None) -> str | None:
    if hotel_slug is None:
        return get_settings().WA_BUSINESS_ACCOUNT_ID
    cur.execute("SELECT waba_id FROM brf_wa_accounts WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    return row["waba_id"] if row else None


# ─── Submit ───


def submit_template(cur: Any, hotel_slug: str | None, *, name: str, language: str, category: str,
                    components: list[dict]) -> dict:
    """Validate and submit one owner-approved template version to Meta.
    400 invalid_category / invalid_template / promo_words; 409 not_connected
    (a hotel without its WABA) or not_configured (no platform WABA). Outside
    live mode the row is recorded as PENDING and nothing is called."""
    from clefs import notify
    from clefs.core.errors import NotConfigured
    from clefs.core.oauth import NotConnected

    cat = (category or "").upper()
    if cat not in CATEGORIES:
        raise ClefsError(400, f"category must be one of {', '.join(CATEGORIES)}", "invalid_category")
    if not re.fullmatch(r"[a-z0-9_]{1,512}", name or ""):
        raise ClefsError(400, "A template name is lowercase letters, digits and _", "invalid_template")
    try:
        notify.validate_template_definition(components)
    except notify.PayloadError as exc:
        raise ClefsError(400, str(exc), "invalid_template") from exc
    hits = lint_definition(components) if cat == "UTILITY" else []
    if hits:
        raise ClefsError(400, f"Promotional words make Meta file this as marketing: {', '.join(sorted(set(hits)))}",
                         "promo_words")
    waba = waba_for(cur, hotel_slug)
    if not waba:
        if hotel_slug is None:
            raise NotConfigured("WhatsApp Business (platform)")
        raise NotConnected("WhatsApp Business is not connected for this hotel", provider="whatsapp")
    status, meta_id, meta_cat = "PENDING", None, None
    try:
        body = _graph("POST", f"{waba}/message_templates",
                      json={"name": name, "language": language, "category": cat, "components": components})
        status = str(body.get("status") or "PENDING").upper()
        meta_id = str(body["id"]) if body.get("id") else None
        meta_cat = (body.get("category") or None) and str(body["category"]).upper()
    except NotLive:
        log.info("template %s/%s recorded as PENDING (NOTIFY_MODE is not live)", name, language)
    row = upsert(cur, hotel_slug, name, language, category_expected=cat, category=meta_cat or cat, status=status,
                 components=components, meta_template_id=meta_id, submitted_at=clock.now(), rejected_reason=None)
    return {"name": row["name"], "language": row["language"], "status": row["status"],
            "meta_template_id": row["meta_template_id"]}


# ─── Sync and alerts ───

_SYNC_FIELDS = "id,name,language,status,category,quality_score,rejected_reason"
MAX_PAGES = 10


def _fetch_all(waba: str) -> list[dict]:
    out: list[dict] = []
    params: dict[str, Any] = {"fields": _SYNC_FIELDS, "limit": 200}
    for _ in range(MAX_PAGES):
        body = _graph("GET", f"{waba}/message_templates", params=params)
        out += list(body.get("data") or [])
        after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not (body.get("paging") or {}).get("next"):
            break
        params = {**params, "after": after}
    return out


def sync(cur: Any, hotel_slug: str | None = None) -> dict:
    """Store Meta's view of one WABA's templates, then alert on changes.
    -> {synced, alerts[], skipped?}."""
    waba = waba_for(cur, hotel_slug)
    if not waba:
        return {"synced": 0, "alerts": [], "skipped": "not_connected"}
    try:
        remote = _fetch_all(waba)
    except NotLive:
        return {"synced": 0, "alerts": [], "skipped": "not_live"}
    now = clock.now()
    for t in remote:
        if not t.get("name") or not t.get("language"):
            continue
        quality = (t.get("quality_score") or {}).get("score") if isinstance(t.get("quality_score"), dict) else None
        upsert(
            cur, hotel_slug, str(t["name"]), str(t["language"]),
            status=str(t.get("status") or "UNKNOWN").upper(),
            category=(str(t["category"]).upper() if t.get("category") else None),
            quality=quality, rejected_reason=(t.get("rejected_reason") or None) if t.get("rejected_reason") != "NONE" else None,
            meta_template_id=str(t["id"]) if t.get("id") else None, last_synced_at=now,
        )
    return {"synced": len(remote), "alerts": check_alerts(cur, hotel_slug)}


def check_alerts(cur: Any, hotel_slug: str | None) -> list[dict]:
    """Alert once per change of (status, category) for a template Meta
    paused, rejected or disabled, or moved out of the category we built it
    for (a UTILITY brief filed as MARKETING). A template that recovers
    clears its marker, so a later relapse alerts again."""
    cur.execute(
        """
        SELECT id, hotel_slug, name, language, status, category, category_expected, rejected_reason, alerted_state
        FROM brf_wa_templates WHERE hotel_slug IS NOT DISTINCT FROM %s
        """,
        (hotel_slug,),
    )
    alerts = []
    for r in cur.fetchall():
        cat = r["category"] or r["category_expected"]
        bad = r["status"] in ALERT_STATUSES or cat != r["category_expected"]
        state = f"{r['status']}|{cat}"
        if not bad:
            if r["alerted_state"]:
                cur.execute("UPDATE brf_wa_templates SET alerted_state = NULL WHERE id = %s", (r["id"],))
            continue
        if r["alerted_state"] == state:
            continue
        cur.execute("UPDATE brf_wa_templates SET alerted_state = %s WHERE id = %s", (state, r["id"]))
        alert = {"name": r["name"], "language": r["language"], "status": r["status"], "category": cat,
                 "category_expected": r["category_expected"], "reason": r["rejected_reason"]}
        alerts.append(alert)
        _raise_alert(cur, hotel_slug, alert)
    return alerts


def _raise_alert(cur: Any, hotel_slug: str | None, a: dict) -> None:
    what = (f"was {a['status'].lower()} by Meta" if a["status"] in ALERT_STATUSES
            else f"was recategorised {a['category']} (built as {a['category_expected']})")
    title = f"WhatsApp template {a['name']} ({a['language']}) {what}"
    if hotel_slug is None:
        # Platform templates (the owner brief itself): Clefs operations fixes
        # these; every hotel's brief falls back to email meanwhile.
        log.error("%s; reason=%s", title, a.get("reason"))
        return
    core_signals.upsert_signal(cur, hotel_slug, core_signals.SignalIn(
        module="brief", kind="wa.template_alert",
        dedupe_key=f"wa.template_alert:{a['name']}:{a['language']}:{a['status']}:{a['category']}",
        title=title[:200], summary=a.get("reason") or "Sends that need this template fall back to email.",
        severity=3, confidence=1.0, owner_role="gm",
        evidence=[core_signals.Evidence(kind="row", label="Template", value=f"{a['name']}/{a['language']}")],
    ))


core_signals.register_signal_kind(
    "wa.template_alert", label="WhatsApp template needs attention", default_owner_role="gm",
    section="service_risk", cadence="daily", icon="whatsapp",
)


# ─── Job: wa.template_sync (daily, every WABA) ───

from clefs import jobs  # noqa: E402 - registration at import, after the functions it calls


@jobs.scheduled("wa.template_sync", jobs.daily_at("04:30", tz="utc"), per_hotel=False)
def _template_sync_job(ctx: jobs.JobCtx) -> dict:
    cur = ctx.cur
    ensure_platform_templates(cur)
    cur.execute("SELECT hotel_slug FROM brf_wa_accounts ORDER BY hotel_slug")
    scopes: list[str | None] = [None, *[r["hotel_slug"] for r in cur.fetchall()]]
    out: dict[str, Any] = {"scopes": {}, "alerts": 0}
    for scope in scopes:
        cur.execute("SAVEPOINT brf_tpl_sync")
        try:
            res = sync(cur, scope)
            cur.execute("RELEASE SAVEPOINT brf_tpl_sync")
        except Exception as exc:  # noqa: BLE001 - one WABA failing must not stop the others
            cur.execute("ROLLBACK TO SAVEPOINT brf_tpl_sync")
            log.warning("template sync failed for %s", scope or "platform", exc_info=True)
            res = {"error": str(exc)[:200]}
        out["scopes"][scope or "platform"] = {k: v for k, v in res.items() if k != "alerts"}
        out["alerts"] += len(res.get("alerts") or [])
    from clefs.modules.brief import nonce

    out["nonces_purged"] = nonce.purge_expired(cur)
    cur.execute("DELETE FROM brf_wa_sessions WHERE expires_at < %s", (clock.now() - timedelta(days=1),))
    return out
