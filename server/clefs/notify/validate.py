"""
WhatsApp Cloud API payload builders and the limits Meta enforces.

Meta rejects an over-long field at send time with a generic error, often long
after the code that built it ran (in the outbox worker). Validating while the
message is built turns that into a PayloadError at the caller, in tests.

Limits (Cloud API, 2026):
  template header text          60      template body            1024
  template footer               60      quick-reply button text    25
  buttons per template          10
  interactive reply buttons      3      reply button title         20
  list: rows in total           10      row title                  24
        row description         72      row / button id           200
        section title           24      list button text           20
  interactive header/footer     60      interactive body         1024
  text body                   4096      document caption         1024
"""

from __future__ import annotations

import re
from typing import Any

TEMPLATE_HEADER_MAX = 60
TEMPLATE_BODY_MAX = 1024
TEMPLATE_FOOTER_MAX = 60
QUICK_REPLY_TEXT_MAX = 25
TEMPLATE_BUTTONS_MAX = 10
BUTTON_PAYLOAD_MAX = 256
REPLY_BUTTONS_MAX = 3
REPLY_TITLE_MAX = 20
LIST_ROWS_MAX = 10
LIST_SECTIONS_MAX = 10
ROW_TITLE_MAX = 24
ROW_DESC_MAX = 72
ID_MAX = 200
SECTION_TITLE_MAX = 24
LIST_BUTTON_MAX = 20
INTERACTIVE_HEADER_MAX = 60
INTERACTIVE_BODY_MAX = 1024
INTERACTIVE_FOOTER_MAX = 60
TEXT_BODY_MAX = 4096
CAPTION_MAX = 1024
FILENAME_MAX = 240


class PayloadError(ValueError):
    """A message that WhatsApp (or the email/SMS provider) would reject."""


def _check_len(label: str, value: str | None, limit: int, *, required: bool = False) -> None:
    if value is None or value == "":
        if required:
            raise PayloadError(f"{label} is required")
        return
    if not isinstance(value, str):
        raise PayloadError(f"{label} must be text")
    if len(value) > limit:
        raise PayloadError(f"{label} is {len(value)} characters; the limit is {limit}")


def wa_to(e164: str) -> str:
    """Cloud API recipients are digits only (the wa_id form)."""
    return e164.lstrip("+")


def _base(to: str, kind: str) -> dict:
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": wa_to(to), "type": kind}


# ─── Templates ───


def template_payload(
    to: str,
    name: str,
    lang: str,
    params: list[Any] | None = None,
    *,
    header: str | dict | None = None,
    buttons: list[dict] | None = None,
) -> dict:
    """A template send. `params` fill the body's {{1}}..{{n}}; `header` is a
    text parameter or {"type": "document"|"image", "link": url, "filename"?};
    `buttons` are {"type": "quick_reply", "payload": str} or
    {"type": "url", "text": str} (the dynamic URL suffix), in button order."""
    _check_len("template name", name, 512, required=True)
    _check_len("template language", lang, 15, required=True)
    components: list[dict] = []
    if header is not None:
        if isinstance(header, str):
            _check_len("template header", header, TEMPLATE_HEADER_MAX)
            components.append({"type": "header", "parameters": [{"type": "text", "text": header}]})
        elif isinstance(header, dict) and header.get("type") in ("document", "image", "video"):
            media = {k: v for k, v in header.items() if k in ("link", "id", "filename")}
            if not media.get("link") and not media.get("id"):
                raise PayloadError("a media header needs a link or a media id")
            components.append({"type": "header", "parameters": [{"type": header["type"], header["type"]: media}]})
        else:
            raise PayloadError("header must be text or a media dict")
    body = [str(p) for p in (params or [])]
    if sum(len(p) for p in body) > TEMPLATE_BODY_MAX:
        raise PayloadError(f"template body parameters exceed {TEMPLATE_BODY_MAX} characters")
    if any(p == "" for p in body):
        raise PayloadError("template parameters cannot be empty (Meta rejects them)")
    if body:
        components.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in body]})
    btns = buttons or []
    if len(btns) > TEMPLATE_BUTTONS_MAX:
        raise PayloadError(f"a template has at most {TEMPLATE_BUTTONS_MAX} buttons")
    for i, b in enumerate(btns):
        kind = b.get("type")
        if kind == "quick_reply":
            _check_len("quick reply payload", b.get("payload"), BUTTON_PAYLOAD_MAX, required=True)
            components.append(
                {"type": "button", "sub_type": "quick_reply", "index": str(i), "parameters": [{"type": "payload", "payload": b["payload"]}]}
            )
        elif kind == "url":
            _check_len("url button suffix", b.get("text"), 2000, required=True)
            components.append({"type": "button", "sub_type": "url", "index": str(i), "parameters": [{"type": "text", "text": b["text"]}]})
        else:
            raise PayloadError(f"button {i}: type must be quick_reply or url")
    payload = _base(to, "template")
    payload["template"] = {"name": name, "language": {"code": lang}}
    if components:
        payload["template"]["components"] = components
    return payload


def validate_template_definition(components: list[dict]) -> None:
    """Check a template before submitting it to Meta (Brief's submit_template):
    header <= 60, body <= 1024, footer <= 60, quick replies <= 25, <= 10 buttons."""
    for c in components:
        kind = (c.get("type") or "").upper()
        if kind == "HEADER" and (c.get("format") or "TEXT").upper() == "TEXT":
            _check_len("template header", c.get("text"), TEMPLATE_HEADER_MAX, required=True)
        elif kind == "BODY":
            _check_len("template body", c.get("text"), TEMPLATE_BODY_MAX, required=True)
        elif kind == "FOOTER":
            _check_len("template footer", c.get("text"), TEMPLATE_FOOTER_MAX, required=True)
        elif kind == "BUTTONS":
            btns = c.get("buttons") or []
            if len(btns) > TEMPLATE_BUTTONS_MAX:
                raise PayloadError(f"a template has at most {TEMPLATE_BUTTONS_MAX} buttons")
            for b in btns:
                if (b.get("type") or "").upper() == "QUICK_REPLY":
                    _check_len("quick reply text", b.get("text"), QUICK_REPLY_TEXT_MAX, required=True)
                else:
                    _check_len("button text", b.get("text"), QUICK_REPLY_TEXT_MAX, required=True)


# ─── Session messages (inside the 24 h window) ───


def text_payload(to: str, body: str, *, preview_url: bool = False) -> dict:
    _check_len("text body", body, TEXT_BODY_MAX, required=True)
    payload = _base(to, "text")
    payload["text"] = {"body": body, "preview_url": bool(preview_url)}
    return payload


def _header_footer(interactive: dict, header: str | None, footer: str | None) -> None:
    if header:
        _check_len("interactive header", header, INTERACTIVE_HEADER_MAX)
        interactive["header"] = {"type": "text", "text": header}
    if footer:
        _check_len("interactive footer", footer, INTERACTIVE_FOOTER_MAX)
        interactive["footer"] = {"text": footer}


def buttons_payload(
    to: str, body: str, buttons: list[tuple[str, str]], *, header: str | None = None, footer: str | None = None
) -> dict:
    """Reply buttons: at most 3, (id, title) with title <= 20 characters."""
    _check_len("interactive body", body, INTERACTIVE_BODY_MAX, required=True)
    if not buttons:
        raise PayloadError("at least one button is required")
    if len(buttons) > REPLY_BUTTONS_MAX:
        raise PayloadError(f"at most {REPLY_BUTTONS_MAX} reply buttons")
    ids = set()
    out = []
    for bid, title in buttons:
        _check_len("button id", bid, ID_MAX, required=True)
        _check_len("button title", title, REPLY_TITLE_MAX, required=True)
        if bid in ids:
            raise PayloadError("button ids must be unique")
        ids.add(bid)
        out.append({"type": "reply", "reply": {"id": bid, "title": title}})
    interactive: dict = {"type": "button", "body": {"text": body}, "action": {"buttons": out}}
    _header_footer(interactive, header, footer)
    payload = _base(to, "interactive")
    payload["interactive"] = interactive
    return payload


def list_payload(
    to: str,
    body: str,
    sections: list[dict],
    *,
    button_text: str = "Choose",
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """A list message. sections = [{"title": str?, "rows": [{"id", "title", "description"?}]}];
    at most 10 rows across all sections."""
    _check_len("interactive body", body, INTERACTIVE_BODY_MAX, required=True)
    _check_len("list button text", button_text, LIST_BUTTON_MAX, required=True)
    if not sections:
        raise PayloadError("a list needs at least one section")
    if len(sections) > LIST_SECTIONS_MAX:
        raise PayloadError(f"at most {LIST_SECTIONS_MAX} sections")
    total = 0
    ids = set()
    out = []
    for s in sections:
        rows = s.get("rows") or []
        if not rows:
            raise PayloadError("every section needs rows")
        if len(sections) > 1:
            _check_len("section title", s.get("title"), SECTION_TITLE_MAX, required=True)
        else:
            _check_len("section title", s.get("title"), SECTION_TITLE_MAX)
        total += len(rows)
        clean_rows = []
        for r in rows:
            _check_len("row id", r.get("id"), ID_MAX, required=True)
            _check_len("row title", r.get("title"), ROW_TITLE_MAX, required=True)
            _check_len("row description", r.get("description"), ROW_DESC_MAX)
            if r["id"] in ids:
                raise PayloadError("row ids must be unique")
            ids.add(r["id"])
            row = {"id": r["id"], "title": r["title"]}
            if r.get("description"):
                row["description"] = r["description"]
            clean_rows.append(row)
        section = {"rows": clean_rows}
        if s.get("title"):
            section["title"] = s["title"]
        out.append(section)
    if total > LIST_ROWS_MAX:
        raise PayloadError(f"a list has at most {LIST_ROWS_MAX} rows in total, got {total}")
    interactive: dict = {"type": "list", "body": {"text": body}, "action": {"button": button_text, "sections": out}}
    _header_footer(interactive, header, footer)
    payload = _base(to, "interactive")
    payload["interactive"] = interactive
    return payload


def document_payload(to: str, *, file_id: str, filename: str, caption: str | None = None) -> dict:
    """A document message. The file stays in our store; the outbox swaps
    file_id for a short-lived signed link when it delivers."""
    _check_len("document filename", filename, FILENAME_MAX, required=True)
    _check_len("document caption", caption, CAPTION_MAX)
    doc = {"file_id": str(file_id), "filename": filename}
    if caption:
        doc["caption"] = caption
    payload = _base(to, "document")
    payload["document"] = doc
    return payload


# ─── Email headers ───
# send_email(headers=...) carries List-Unsubscribe / List-Unsubscribe-Post for
# marketing mail (04 spec F4) and In-Reply-To / References for inquiry
# threading. Resend takes them as JSON, so this is not about SMTP injection
# as such; it stops a CR/LF or an address header from slipping past the
# explicit to / reply_to / from_ arguments, which are what the outbox records.

EMAIL_HEADER_NAME_MAX = 76
EMAIL_HEADER_VALUE_MAX = 998
EMAIL_RESERVED_HEADERS = frozenset(
    {"from", "to", "cc", "bcc", "subject", "reply-to", "sender", "return-path", "date", "mime-version",
     "content-type", "content-transfer-encoding"}
)
# Every Content-* header is reserved, not only the two named above
# (CONTRACTS Part 2): Content-Disposition: attachment at the top level makes
# a client render the whole message as an attachment, and Content-ID /
# Content-Language would describe a body the outbox record does not hold.
# The MIME structure is Resend's, built from send_email's own arguments.
EMAIL_RESERVED_PREFIXES = ("content-",)


def email_headers(headers: dict[str, Any] | None) -> list[dict]:
    """{"List-Unsubscribe": "<https://...>"} -> our email payload's
    [{"Name", "Value"}] (the transport turns it into Resend's headers object)."""
    out: list[dict] = []
    for name, value in (headers or {}).items():
        n = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", n) or len(n) > EMAIL_HEADER_NAME_MAX:
            raise PayloadError(f"not a valid email header name: {name!r}")
        if n.lower() in EMAIL_RESERVED_HEADERS or n.lower().startswith(EMAIL_RESERVED_PREFIXES):
            raise PayloadError(f"set {n} through send_email's own arguments, not headers")
        v = "" if value is None else str(value)
        if "\r" in v or "\n" in v or len(v) > EMAIL_HEADER_VALUE_MAX:
            raise PayloadError(f"email header {n} must be one line of at most {EMAIL_HEADER_VALUE_MAX} characters")
        out.append({"Name": n, "Value": v})
    return out
