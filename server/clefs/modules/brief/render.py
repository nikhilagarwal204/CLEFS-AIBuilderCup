"""
Brief rendering: the plain lines (WhatsApp template variables, SMS, email
text) and the HTML email. Pure functions over a run dict (brief.get_run).

Line format (02 spec 6.2):
    "1) AED 1,020 · Booking no-show commission · 90% · by 14 Oct · Finance"

The WhatsApp brief is a UTILITY template (Canon: WhatsApp): `lint` strips
promotional words from variables so Meta never recategorises it as
marketing, and `budget_lines` keeps the body under the 1,024 limit.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from clefs.core import money

BODY_LIMIT = 1024  # WhatsApp template body parameters in total (CONTRACTS notify)
TITLE_MIN = 12  # never shorten a title below this many characters

# ─── Promo-word lint ───

PROMO_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\bclose discount plans\b", "restrict rate plans"),
    (r"\bdiscount(s|ed)?\b", "rate change"),
    (r"\b\d+\s*%\s*off\b", "rate change"),
    (r"%\s*off\b", ""),
    (r"\boffers?\b", "rate"),
    (r"\bdeals?\b", "booking"),
    (r"\bsales?\b", "booking"),
    (r"\bpromos?\b", "rate"),
    (r"\bpromotions?\b", "rate"),
    (r"\bfree\b", "no-charge"),
)


def lint(text: str) -> str:
    """Strip promotional wording from a template variable (case-insensitive)."""
    out = text or ""
    for pattern, repl in PROMO_REPLACEMENTS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip()


# ─── Lines ───


def _fmt_deadline(iso: str | None, tz: str) -> str | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso) if isinstance(iso, str) else iso
    local = dt.astimezone(ZoneInfo(tz))
    return f"by {local.day} {local.strftime('%b')}"


def is_estimate(item: dict) -> bool:
    """An item whose money figure is an estimate (brief.impact_estimated,
    stored on the item): its amount reads "AED 2,500 est." everywhere."""
    return bool(item.get("impact_estimated") and item.get("impact_minor"))


def any_estimate(run: dict) -> bool:
    return any(is_estimate(i) for i in list(run.get("items") or []) + list(run.get("overdue") or []))


def stake_text(minor: int | None, currency: str, locale: str | None, *, estimated: bool) -> str:
    """The at-stake total ("AED 6,420.00"), with "(incl. est.)" when it sums
    an estimate with statement-backed money."""
    out = money.fmt_money(minor or 0, currency, locale, minor=True)
    return f"{out} (incl. est.)" if estimated else out


def item_line(item: dict, *, tz: str, locale: str | None = "en-AE", title_max: int | None = None) -> str:
    title = item.get("title") or ""
    if title_max is not None and len(title) > title_max:
        title = title[: max(TITLE_MIN, title_max) - 1].rstrip() + "…"
    parts = []
    if item.get("impact_minor") and item.get("currency"):
        amount = money.fmt_money(item["impact_minor"], item["currency"], locale, minor=True).replace(".00", "")
        parts.append(f"{amount} est." if is_estimate(item) else amount)
    parts.append(title)
    if item.get("confidence") is not None:
        parts.append(f"{round(float(item['confidence']) * 100)}%")
    dl = _fmt_deadline(item.get("deadline_at"), tz)
    if dl:
        parts.append(dl)
    if item.get("responsible_label"):
        parts.append(str(item["responsible_label"]))
    prefix = f"{item.get('item_no')}) " if item.get("item_no") else ""
    return prefix + " · ".join(parts)


def run_lines(run: dict, *, tz: str, locale: str | None = "en-AE", title_max: int | None = None) -> list[str]:
    """Overdue items first (they are pinned), then the ranked ones."""
    items = list(run.get("overdue") or []) + list(run.get("items") or [])
    return [item_line(i, tz=tz, locale=locale, title_max=title_max) for i in items]


def budget_lines(run: dict, *, tz: str, locale: str | None = "en-AE", fixed: int = 0,
                 slots: int = 5) -> list[str]:
    """`slots` linted lines whose total (plus `fixed` characters of other
    variables) stays within BODY_LIMIT: titles shrink until it fits."""
    title_max = 80
    while True:
        lines = [lint(x) for x in run_lines(run, tz=tz, locale=locale, title_max=title_max)][:slots]
        lines += ["-"] * (slots - len(lines))
        if fixed + sum(len(x) for x in lines) <= BODY_LIMIT or title_max <= TITLE_MIN:
            return [x[: BODY_LIMIT // slots] for x in lines] if title_max <= TITLE_MIN else lines
        title_max -= 8


def text(run: dict, *, tz: str, locale: str | None = "en-AE", link: str | None = None) -> str:
    """The plain-text brief (email text part, logs)."""
    out = [f"Clefs brief · {run.get('brief_date')}", run.get("headline") or ""]
    out += run_lines(run, tz=tz, locale=locale)
    ver = (run.get("verified") or {}).get("text")
    if ver:
        out.append(ver)
    if link:
        out.append(f"Open the brief: {link}")
    return "\n".join(x for x in out if x)


# ─── HTML email ───

_SECTION_LABELS = {
    "money_lost": "Money lost", "money_available": "Money available", "service_risk": "Service risk",
    "reviews": "Reviews", "verified": "Verified", "compliance": "Compliance",
}


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _item_html(item: dict, *, tz: str, locale: str | None, overdue: bool = False) -> str:
    impact = (
        money.fmt_money(item["impact_minor"], item["currency"], locale, minor=True)
        + (" est." if is_estimate(item) else "")
        if item.get("impact_minor") and item.get("currency") else "No money figure"
    )
    conf = f"{round(float(item['confidence']) * 100)}% confidence" if item.get("confidence") is not None else ""
    dl = _fmt_deadline(item.get("deadline_at"), tz) or "no deadline"
    rec = (item.get("recommended_action") or {}).get("label") or ""
    badge = "Overdue" if overdue else (item.get("section_label")  # "Finance to-do", brief.section_label
                                       or _SECTION_LABELS.get(item.get("section") or "", item.get("section") or ""))
    return (
        '<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
        f'<div style="font-size:12px;color:{"#b91c1c" if overdue else "#6b7280"}">{_e(item.get("item_no"))}. {_e(badge)}</div>'
        f'<div style="font-size:16px;font-weight:600;color:#111827">{_e(item.get("title"))}</div>'
        f'<div style="font-size:14px;color:#374151">{_e(impact)} · {_e(conf)} · {_e(dl)} · {_e(item.get("responsible_label"))}</div>'
        + (f'<div style="font-size:13px;color:#4b5563">Recommended: {_e(rec)}</div>' if rec else "")
        + "</td></tr>"
    )


def email_html(run: dict, *, hotel_name: str, tz: str, locale: str | None = "en-AE", link: str | None = None) -> str:
    """A single-column email that reads well on a phone. Every decision
    happens behind the magic link, so the email itself carries no buttons
    that change state."""
    rows = [_item_html(i, tz=tz, locale=locale, overdue=True) for i in run.get("overdue") or []]
    rows += [_item_html(i, tz=tz, locale=locale) for i in run.get("items") or []]
    blocks = run.get("blocks") or {}
    extra = []
    auto = blocks.get("autonomy") or {}
    for p in auto.get("promotions") or []:
        extra.append(f"<li>{_e(p.get('text'))}</li>")
    for d in auto.get("demotions") or []:
        extra.append(f"<li>{_e(d.get('text'))}</li>")
    for c in blocks.get("compliance") or []:
        extra.append(f"<li>Compliance: {_e(c.get('title'))}</li>")
    fin = blocks.get("finance") or {}
    if fin.get("kind") == "pl_pulse" and not fin.get("available"):
        extra.append("<li>P&amp;L pulse: not published yet for last month.</li>")
    rep = blocks.get("report") or {}
    if rep.get("text"):
        extra.append(f"<li>{_e(rep['text'])}</li>")
    ver = (run.get("verified") or {}).get("text") or ""
    button = (
        f'<p><a href="{_e(link)}" style="display:inline-block;background:#111827;color:#ffffff;padding:12px 20px;'
        'border-radius:8px;text-decoration:none;font-weight:600">Open the brief</a></p>'
        if link else ""
    )
    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:#f9fafb">'
        '<div style="max-width:560px;margin:0 auto;padding:24px 16px;font-family:-apple-system,Segoe UI,Roboto,sans-serif">'
        f'<div style="font-size:13px;color:#6b7280">{_e(hotel_name)} · {_e(run.get("cadence"))} brief · {_e(run.get("brief_date"))}</div>'
        f'<h1 style="font-size:20px;color:#111827;margin:8px 0 16px">{_e(run.get("headline"))}</h1>'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{"".join(rows)}</table>'
        + (f'<ul style="font-size:14px;color:#374151;padding-left:18px">{"".join(extra)}</ul>' if extra else "")
        + f'<p style="font-size:14px;color:#065f46;font-weight:600">{_e(ver)}</p>'
        + button
        + '<p style="font-size:12px;color:#9ca3af">The link signs you in for 72 hours and only decides the items above.</p>'
        "</div></body></html>"
    )
