"""
Ask Clefs on WhatsApp (spec 7): free text from an opted-in owner, GM or
finance member is answered inside the 24 h customer-service window, as text
(up to 10 rows as aligned lines) plus a magic link to the console answer.

Dispatch order: the brief's handler (priority 40) takes Adjust replies and
STOP/START first and passes every other text on; this one runs at 60. It
never sends a template: outside the window there is no answer to give.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from clefs import notify
from clefs.auth import magic
from clefs.auth.deps import Principal, resolve_ctx
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.modules.ask import engine, store, summary
from clefs.modules.ask.constants import WA_MAGIC_TTL_H, WA_MAX_ROWS, WA_ROLES
from clefs.settings import get_settings

PURPOSE = "ask.whatsapp_answer"
MAX_BODY = 3900  # under Meta's 4096 with room for the link line
_SKIP_WORDS = frozenset({"STOP", "START", "UNSUBSCRIBE", "STOP ALL", "HELP"})
_PREFIX_RE = re.compile(r"^\s*([a-z0-9][a-z0-9_-]{1,62})\s*:\s*(.+)$", re.S | re.I)


# ─── Routing ───


def _match(ctx: notify.InboundCtx) -> bool:
    """Read-only: an opted-in decision maker sent free text."""
    if ctx.user_id is None or not (ctx.text or "").strip():
        return False
    if ctx.text.strip().upper() in _SKIP_WORDS:
        return False
    return bool(store.optins_for_user(ctx.cur, int(ctx.user_id)))


def pick_hotel(optins: list[dict], text: str, hinted: str | None) -> tuple[str, str]:
    """(hotel_slug, question). "<slug>: question" picks a hotel explicitly;
    else the hotel the number belongs to, else the one asked most recently."""
    slugs = [o["hotel_slug"] for o in optins]
    m = _PREFIX_RE.match(text)
    if m and m.group(1).lower() in slugs:
        return m.group(1).lower(), m.group(2).strip()
    if hinted in slugs:
        return str(hinted), text.strip()
    return slugs[0], text.strip()


def _window_open(ctx: notify.InboundCtx) -> bool:
    until = ctx.window_open_until
    if until is None and ctx.sender_hmac is not None and ctx.hotel_slug:
        until = notify.wa_window_open_until(ctx.cur, ctx.hotel_slug, sender_hmac=ctx.sender_hmac)
    # A message that just arrived opens the window; None means the platform
    # number's window, which the inbound itself opened a moment ago.
    return until is None or until > clock.now()


# ─── Reply ───


def _link(cur: Any, user_id: int, slug: str, log_id: int) -> str:
    token = magic.create_link(
        cur, user_id=user_id, hotel_slug=slug, purpose="login", next_path=f"/app/{slug}/ask?log={log_id}",
        scope={"ask_log_ids": [log_id]}, ttl=timedelta(hours=WA_MAGIC_TTL_H), max_uses=3,
    )
    return f"{get_settings().web_base_url}{magic.link_path(token)}"


def format_reply(out: dict, *, locale: str | None, link: str | None) -> str:
    parts = [out.get("answer_text") or ""]
    results = out.get("results") or []
    if results and out.get("table") and (out["table"].get("rows") or []):
        block = results[0]
        rows = block["table"]["rows"]
        spec_like = _SpecView(block)
        res_like = {"unit": block["trace"]["unit"], "currency": block["trace"]["currency"],
                    "label": block["trace"]["metric_label"], "rows": spec_like.rows(rows)}
        ls = summary.lines(res_like, spec_like, max_rows=WA_MAX_ROWS, locale=locale)
        if len(ls) > 1 or block["trace"]["dims"] or block["trace"]["grain"] != "total":
            parts.append("```\n" + "\n".join(ls) + "\n```")
        more = block["table"]["row_count"] - min(len(rows), WA_MAX_ROWS)
        if more > 0:
            parts.append(f"+{more} more rows in the console.")
    if out.get("suggestions"):
        parts.append("Try: " + "; ".join(out["suggestions"][:3]))
    body = "\n\n".join(p for p in parts if p)
    if len(body) > MAX_BODY:
        body = body[: MAX_BODY - 3] + "..."
    if link:
        body += f"\n\nOpen in Clefs: {link}"
    return body


class _SpecView:
    """Just enough of a QuerySpec, rebuilt from a stored result block, for
    summary.lines (the reply is formatted from what the log holds)."""

    def __init__(self, block: dict) -> None:
        t = block["trace"]
        self.grain = t["grain"]
        self.dimensions = list(t["dims"])
        self.compare = t["compare"]
        self._cols = [c["key"] for c in block["table"]["columns"]]

    def rows(self, table_rows: list[list]) -> list[dict]:
        out = []
        for r in table_rows:
            cell = dict(zip(self._cols, r))
            out.append({"bucket": cell.get("bucket"), "group": {d: cell.get(d) for d in self.dimensions},
                        "value": cell.get("value"), "compare_value": cell.get("compare_value")})
        return out


# ─── Handler ───


def _send(ictx: notify.InboundCtx, body: str) -> Any:
    return ictx.reply_text(body, purpose=PURPOSE, idempotency_key=f"ask:wa:{ictx.provider_id}")


@notify.on_whatsapp("staff", priority=60, kind="text", match=_match)
def on_text(ictx: notify.InboundCtx) -> dict:
    cur, uid = ictx.cur, int(ictx.user_id)
    if not _window_open(ictx):
        return {"handled": True, "outcome": "outside_window"}  # nothing may be sent; never a template
    optins = store.optins_for_user(cur, uid)
    if not optins:
        return {"handled": False}
    slug, question = pick_hotel(optins, ictx.text or "", ictx.hotel_slug)
    hctx = resolve_ctx(Principal(kind="user", user_id=uid, session_kind="whatsapp", label="whatsapp"), slug, cur=cur)
    if hctx.role not in WA_ROLES or not hctx.can("ask.view"):
        return {"handled": False}  # role changed since opting in: let other handlers try
    store.mark_asked(cur, slug, uid)
    try:
        out = engine.ask(cur, hctx, question, channel="WHATSAPP")
    except ClefsError as exc:
        if exc.code != "rate_limited":
            raise
        _send(ictx, exc.detail)
        return {"handled": True, "outcome": "rate_limited", "hotel": slug}
    link = _link(cur, uid, slug, out["log_id"]) if out.get("results") else None
    others = [o["hotel_slug"] for o in optins if o["hotel_slug"] != slug]
    body = format_reply(out, locale=hctx.hotel.locale, link=link)
    if others:
        body = f"({hctx.hotel.name}) " + body + f"\n\nFor another hotel, start with its code, e.g. \"{others[0]}: ...\""
    _send(ictx, body)
    return {"handled": True, "outcome": out["status"], "hotel": slug, "log_id": out["log_id"]}
