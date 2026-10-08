"""Dispute packets (02 spec 3.7).

`create_packet` groups open RECOVERABLE findings of one OTA into a DRAFT
packet: the booking references, the contract clause each claim rests on,
the calculation trace, the evidence and the approval stamp. It is rendered
four ways:
- HTML, server-side and print-optimised (GET /disputes/{id}/print);
- PDF through fpdf2 (pure Python, runs on Vercel; WhatsApp needs a document);
- extranet text of 1,000 characters or fewer, one template per OTA and
  detector (the extranet's dispute box is that small);
- an email subject and body.

Lifecycle: DRAFT -> APPROVED (owner or finance only) -> SUBMITTED ->
ACKNOWLEDGED -> CREDITED | PARTIAL | REJECTED. Each packet carries one
`pg.dispute` action on the dispute path (always approve), so the brief
shows it and Finance counts the verified credit. Submission snapshots the
invoiced commission per booking into the findings' DISPUTED events, which
is how auto-verification (ledger.py) recognises a corrected invoice.
`pg.dispute_followup` reminds every 10 business days while a submitted
packet has no credit.
"""

from __future__ import annotations

import html as _html
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from clefs import db, files
from clefs.audit import audit
from clefs.core import actions, clock, money
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import actions as _pg_actions  # noqa: F401  (registers pg.dispute)
from clefs.modules.profit_guard import ledger
from clefs.modules.profit_guard import signals as pg_signals

PACKET_STATUSES = ("DRAFT", "APPROVED", "SUBMITTED", "ACKNOWLEDGED", "CREDITED", "PARTIAL", "REJECTED")
OPEN_PACKET = ("DRAFT", "APPROVED", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL")
APPROVER_ROLES = ("owner", "finance")
APPROVE_PERM = "profit.approve_disputes"   # FOUNDATION-REQUEST: module.py grants it to owner + finance
EXTRANET_MAX = 1000
FOLLOW_UP_BUSINESS_DAYS = 10
RETENTION_DAYS = 400                        # OTA disputes run past the next statements
ACTION_TYPE = "pg.dispute"
MAX_FINDINGS = 200

# ─── Texts (one template per OTA and detector) ───

OTA_NAMES = pg_signals.OTA_NAMES
OTA_ASK = {
    "BOOKING": "please issue a credit note.",
    "AGODA": "please correct it on the next invoice.",
    "EXPEDIA": "please adjust it in the Partner Central reconciliation.",
}
DEFAULT_ASK = "please issue a credit note."
# {ref} booking, {date} stay date, {clause} contract clause, {amount} claim.
LINE_TEMPLATES = {
    "L1": "Reservation {ref}: commission was charged on the municipality fee and Tourism Dirham, which are "
          "configured as charges; per {clause} commission of {amount} on these fees should not apply",
    "L2": "Reservation {ref}: commission was charged above the contracted rate; per {clause} the excess "
          "commission of {amount} should not apply",
    "L3": "Reservation {ref}: guest did not arrive (no-show) {date}; per {clause} commission of {amount} "
          "should not apply",
    "L4": "Reservation {ref}: guest departed early, nights after {date} were not stayed; per {clause} "
          "commission of {amount} on the unstayed nights should not apply",
    "L5": "Reservation {ref}: the commissionable amount is above the room revenue the guest paid; per "
          "{clause} the excess commission of {amount} should not apply",
    "L12": "Reservation {ref}: the guest was refunded but the commission was not reduced; per {clause} "
           "commission of {amount} on the refunded amount should not apply",
    "L10": "Payout {ref}: the amount paid is {amount} short of the statement",
    "L13": "Reservation {ref}: chargeback of {amount} was deducted; we contest it with the attached evidence",
    "L14": "Reservation {ref}: guest did not arrive (no-show) {date}; per {clause} commission of {amount} "
           "should not apply",
}
DEFAULT_TEMPLATE = "Reservation {ref}: commission of {amount} was charged in error ({label})"
LABELS = {"L1": "commission on excluded fees", "L2": "commission rate above contract", "L3": "no-show",
          "L4": "early departure", "L5": "commission base above the folio", "L10": "payout shortfall",
          "L12": "refund without commission credit", "L13": "chargeback", "L14": "no-show"}
# The contract field each claim rests on (models.ClauseCitation.field).
CLAUSE_FIELD = {"L1": "commission_base_rule", "L5": "commission_base_rule", "L2": "base_commission_pct",
                "L3": "dispute_rules", "L4": "dispute_rules", "L14": "dispute_rules", "L12": "commission_base_rule"}
DEFAULT_CLAUSE = {"BOOKING": "GDT v2601 2.3.2(ii)", "AGODA": "the Agoda contract", "EXPEDIA": "the Expedia contract"}


def fmt(hotel: Any, amount_minor: int, currency: str) -> str:
    return money.fmt_money(int(amount_minor), currency, getattr(hotel, "locale", None) or "en-AE", minor=True)


def _date_txt(d: Any) -> str:
    return d.strftime("%d %b %Y") if isinstance(d, (date, datetime)) else (str(d) if d else "")


def item_sentence(hotel: Any, item: dict) -> str:
    tpl = LINE_TEMPLATES.get(item["detector"], DEFAULT_TEMPLATE)
    return tpl.format(ref=item["booking_ref"] or item["id"], date=_date_txt(item.get("stay_date")),
                      clause=item.get("clause_ref") or "the contract", label=LABELS.get(item["detector"], "claim"),
                      amount=fmt(hotel, item["amount_minor"], item["currency"]))


def extranet_text(hotel: Any, ota_code: str, items: list[dict], total_minor: int, currency: str) -> str:
    """One sentence per booking, then the OTA's ask; always <= 1,000
    characters (bookings that do not fit are summarised by count)."""
    ask = OTA_ASK.get(ota_code, DEFAULT_ASK)
    if len(items) == 1:
        text = f"{item_sentence(hotel, items[0])}; {ask}"
        return text if len(text) <= EXTRANET_MAX else text[:EXTRANET_MAX - 1] + "."
    head = f"{hotel.name}: {len(items)} commission corrections, total {fmt(hotel, total_minor, currency)}. "
    tail = f" Details in the attached packet; {ask}"
    body, used = [], len(head) + len(tail)
    for i, it in enumerate(items):
        s = item_sentence(hotel, it) + ". "
        after = len(items) - i - 1
        reserve = len(f"+{after} more bookings. ") if after else 0   # room to say what did not fit
        if used + len(s) + reserve > EXTRANET_MAX:
            body.append(f"+{len(items) - i} more bookings. ")
            break
        body.append(s)
        used += len(s)
    text = (head + "".join(body).rstrip() + tail).strip()
    return text if len(text) <= EXTRANET_MAX else text[:EXTRANET_MAX - 1] + "."


def email_texts(hotel: Any, ota_code: str, packet_id: int, items: list[dict], total_minor: int,
                currency: str) -> tuple[str, str]:
    ota = OTA_NAMES.get(ota_code, ota_code)
    subject = (f"Commission dispute {hotel.name} (packet {packet_id}): {len(items)} booking"
               f"{'s' if len(items) != 1 else ''}, {fmt(hotel, total_minor, currency)}")
    lines = [f"Dear {ota} partner support,", "",
             f"We dispute the following commission charges for {hotel.name}, totalling "
             f"{fmt(hotel, total_minor, currency)}. The calculation and evidence for each are in the attached "
             "packet.", ""]
    lines += [f"- {item_sentence(hotel, it)}." for it in items]
    lines += ["", f"Kindly {OTA_ASK.get(ota_code, DEFAULT_ASK)}", "", "Regards,", hotel.name]
    return subject[:250], "\n".join(lines)


# ─── Packet contents ───


def _clause(cur: Any, hotel_slug: str, ota: str, detector: str, on: date | None) -> tuple[str | None, str | None]:
    """(ref, text) of the contract clause a claim rests on: the confirmed
    contract in force on the stay date (its citation for the claim's
    field, else its first), else the OTA's public terms. An internal
    reference ("Canon ...") never reaches the OTA."""
    from clefs.modules.profit_guard import contracts

    terms = contracts.contract_on(cur, hotel_slug, ota, on or clock.now().date())
    clauses = [c for c in (terms or {}).get("clauses") or [] if isinstance(c, dict) and c.get("ref")]
    want = CLAUSE_FIELD.get(detector)
    c = next((c for c in clauses if c.get("field") == want), None) or (clauses[0] if clauses else None)
    if c is None or str(c["ref"]).lower().startswith("canon"):
        return DEFAULT_CLAUSE.get(ota), (c or {}).get("text")
    return str(c["ref"])[:200], c.get("text")


def packet_items(cur: Any, hotel: Any, finding_ids: list[int]) -> list[dict]:
    """One entry per finding with everything the OTA needs to check it."""
    cur.execute(
        """
        SELECT f.*, l.ota_res_id, l.guest_name, l.check_in AS l_check_in, l.check_out AS l_check_out,
               s.doc_no, s.doc_date, r.pms_res_id, r.status AS res_status, r.check_in AS r_check_in,
               r.check_out AS r_check_out, r.actual_check_out, r.ota_res_id AS r_ota_res_id, r.guest_name AS r_guest
          FROM pfg_findings f
          LEFT JOIN pfg_ota_statement_lines l ON l.id = f.statement_line_id
          LEFT JOIN pfg_ota_statements s ON s.id = l.statement_id
          LEFT JOIN reservations r ON r.id = f.reservation_id
         WHERE f.hotel_slug = %s AND f.id = ANY(%s)
         ORDER BY f.deadline_at NULLS LAST, f.id
        """,
        (hotel.slug, list(finding_ids)),
    )
    items = []
    for f in [dict(r) for r in cur.fetchall()]:
        det = f["detector"]
        check_in = f["l_check_in"] or f["r_check_in"]
        check_out = f["l_check_out"] or f["r_check_out"]
        stay_date = f["actual_check_out"] if det == "L4" else check_in
        ref, text = _clause(cur, hotel.slug, f["ota_code"], det, check_in or f["doc_date"])
        items.append({
            "id": f["id"], "detector": det, "label": LABELS.get(det, det), "status": f["status"],
            "booking_ref": f["ota_res_id"] or f["r_ota_res_id"] or ledger.res_norm_of(f),
            "pms_res_id": f["pms_res_id"], "guest_name": f["guest_name"] or f["r_guest"],
            "check_in": check_in, "check_out": check_out, "stay_date": stay_date,
            "reservation_status": f["res_status"], "doc_no": f["doc_no"], "doc_date": f["doc_date"],
            # The claim is what is still owed (a re-dispute after a partial credit claims the rest).
            "amount_minor": int(f["amount_minor"]) - int(f["recovered_minor"] or 0),
            "finding_amount_minor": int(f["amount_minor"]), "recovered_minor": int(f["recovered_minor"] or 0),
            "currency": f["currency"], "confidence": str(f["confidence"]), "deadline_at": f["deadline_at"],
            "calc": [s for s in (f["calc"] or []) if s.get("label") != "Confidence"],
            "evidence": f["evidence"] or [], "clause_ref": ref, "clause_text": text,
        })
    return items


def evidence_links(hotel_slug: str, items: list[dict]) -> dict[str, str]:
    """Signed download paths for every file an item's evidence names
    (screenshots, the statement PDF): valid a week, for print and email."""
    out: dict[str, str] = {}
    for it in items:
        for e in it["evidence"]:
            ref = e.get("ref") if isinstance(e, dict) else None
            fid = (ref or {}).get("file_id") if isinstance(ref, dict) else None
            if fid and str(fid) not in out:
                try:
                    out[str(fid)] = files.signed_path(hotel_slug, str(fid), ttl_s=7 * 86400)
                except (ClefsError, ValueError):
                    continue
    return out


# ─── Rendering: HTML ───

PRINT_CSS = """
*{box-sizing:border-box}body{font:12px/1.45 -apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:#111;
margin:0;padding:24px;background:#fff}h1{font-size:18px;margin:0 0 4px}h2{font-size:14px;margin:18px 0 6px}
.meta{color:#444;margin-bottom:12px}.stamp{border:2px solid #0a7a3d;color:#0a7a3d;display:inline-block;
padding:6px 10px;font-weight:600;margin:6px 0}.stamp.pending{border-color:#a15c00;color:#a15c00}
table{width:100%;border-collapse:collapse;margin:6px 0}th,td{border:1px solid #bbb;padding:4px 6px;
text-align:left;vertical-align:top}th{background:#f1f1f1}td.num,th.num{text-align:right;
font-variant-numeric:tabular-nums}.item{page-break-inside:avoid;border-top:1px solid #999;padding-top:8px;
margin-top:12px}.clause{border-left:3px solid #888;padding:4px 8px;color:#333;margin:6px 0}
pre{white-space:pre-wrap;font:inherit;background:#f7f7f7;padding:8px;border:1px solid #ddd}
@media print{body{padding:0}@page{size:A4;margin:14mm}a{color:#111}}
"""


def _e(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


def approver_name(cur: Any, packet: dict) -> str | None:
    if not packet.get("approved_by"):
        return None
    cur.execute("SELECT name FROM users WHERE id = %s", (packet["approved_by"],))
    row = cur.fetchone()
    return row["name"] if row else None


def stamp_text(hotel: Any, packet: dict, approver: str | None) -> str:
    if packet.get("approved_at"):
        at = packet["approved_at"].astimezone(hotel.tz).strftime("%d %b %Y %H:%M")
        return f"Approved by {approver or 'the owner'} on {at}"
    return "Draft: awaiting owner or finance approval"


def _calc_rows(hotel: Any, steps: list[dict], currency: str) -> str:
    out = []
    for s in steps:
        res = s.get("result_minor")
        val = fmt(hotel, res, currency) if isinstance(res, int) else (s.get("result") or "")
        inputs = ", ".join(f"{k}={v}" for k, v in (s.get("inputs") or {}).items())
        out.append(f"<tr><td>{_e(s.get('label'))}</td><td>{_e(s.get('formula'))}</td><td>{_e(inputs)}</td>"
                   f"<td class=num>{_e(val)}</td></tr>")
    return "".join(out)


def render_html(hotel: Any, packet: dict, items: list[dict], *, approver: str | None = None,
                links: dict[str, str] | None = None) -> str:
    """The print-optimised packet (self-contained: inline CSS, no scripts)."""
    ccy, links = packet["currency"], links or {}
    ota = OTA_NAMES.get(packet["ota_code"], packet["ota_code"])
    stamp_cls = "stamp" if packet.get("approved_at") else "stamp pending"
    rows = "".join(
        f"<tr><td>{_e(it['booking_ref'])}</td><td>{_e(it['guest_name'])}</td><td>{_e(_date_txt(it['check_in']))}"
        f" to {_e(_date_txt(it['check_out']))}</td><td>{_e(it['label'])}</td><td>{_e(it['doc_no'])}</td>"
        f"<td class=num>{_e(fmt(hotel, it['amount_minor'], it['currency']))}</td></tr>" for it in items)
    blocks = []
    for it in items:
        ev = []
        for e in it["evidence"]:
            if not isinstance(e, dict):
                continue
            fid = str((e.get("ref") or {}).get("file_id") or "") if isinstance(e.get("ref"), dict) else ""
            label = _e(e.get("label") or e.get("kind"))
            ev.append(f"<li><a href=\"{_e(links[fid])}\">{label}</a></li>" if fid in links else f"<li>{label}</li>")
        clause = (f"<div class=clause><b>{_e(it['clause_ref'])}</b>"
                  f"{': ' + _e(it['clause_text']) if it.get('clause_text') else ''}</div>") if it["clause_ref"] else ""
        blocks.append(
            f"<div class=item><h2>Reservation {_e(it['booking_ref'])}: {_e(it['label'])}, "
            f"{_e(fmt(hotel, it['amount_minor'], it['currency']))}</h2>"
            f"<p>{_e(item_sentence(hotel, it))}.</p>{clause}"
            f"<table><tr><th>Step</th><th>Formula</th><th>Inputs</th><th class=num>Result</th></tr>"
            f"{_calc_rows(hotel, it['calc'], it['currency'])}</table>"
            f"{'<ul>' + ''.join(ev) + '</ul>' if ev else ''}</div>")
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<title>Dispute packet {packet['id']} - {_e(hotel.name)}</title><style>{PRINT_CSS}</style></head><body>"
        f"<h1>{_e(ota)} commission dispute: {_e(hotel.name)}</h1>"
        f"<div class=meta>Packet {packet['id']} &middot; {len(items)} booking{'s' if len(items) != 1 else ''} "
        f"&middot; total {_e(fmt(hotel, packet['total_minor'], ccy))} &middot; status {_e(packet['status'])}</div>"
        f"<div class=\"{stamp_cls}\">{_e(stamp_text(hotel, packet, approver))}</div>"
        "<h2>Bookings</h2><table><tr><th>Reservation</th><th>Guest</th><th>Stay</th><th>Issue</th>"
        f"<th>Document</th><th class=num>Amount</th></tr>{rows}</table>"
        f"<h2>Extranet text</h2><pre>{_e(packet.get('extranet_text'))}</pre>"
        f"{''.join(blocks)}</body></html>"
    )


# ─── Rendering: PDF ───


def _latin1(s: Any) -> str:
    """fpdf2's core fonts are Latin-1: anything else becomes '?', never a
    crash (an Arabic guest name still has its booking reference)."""
    return str("" if s is None else s).encode("latin-1", "replace").decode("latin-1")


def render_pdf(hotel: Any, packet: dict, items: list[dict], *, approver: str | None = None) -> bytes:
    from fpdf import FPDF

    ccy = packet["currency"]
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(_latin1(f"Dispute packet {packet['id']}"))
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def para(text: str, size: int = 10, style: str = "", h: float = 5) -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(width, h, _latin1(text), new_x="LMARGIN", new_y="NEXT")

    ota = OTA_NAMES.get(packet["ota_code"], packet["ota_code"])
    para(f"{ota} commission dispute: {hotel.name}", 15, "B", 8)
    para(f"Packet {packet['id']} | {len(items)} booking{'s' if len(items) != 1 else ''} | total "
         f"{fmt(hotel, packet['total_minor'], ccy)} | status {packet['status']}", 10)
    pdf.ln(2)
    pdf.set_text_color(10, 122, 61) if packet.get("approved_at") else pdf.set_text_color(161, 92, 0)
    para(stamp_text(hotel, packet, approver), 11, "B")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    para("Bookings", 12, "B", 7)
    cols = (0.22, 0.26, 0.22, 0.12, 0.18)
    pdf.set_font("Helvetica", "B", 9)
    for label, c in zip(("Reservation", "Guest", "Stay", "Issue", "Amount"), cols):
        pdf.cell(width * c, 6, label, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for it in items:
        cells = (it["booking_ref"], it["guest_name"], f"{_date_txt(it['check_in'])} - {_date_txt(it['check_out'])}",
                 it["label"], fmt(hotel, it["amount_minor"], it["currency"]))
        for i, (text, c) in enumerate(zip(cells, cols)):
            t = _latin1(text)
            while t and pdf.get_string_width(t) > width * c - 2:
                t = t[:-1]
            pdf.cell(width * c, 6, t, border=1, align="R" if i == 4 else "L")
        pdf.ln()
    pdf.ln(3)
    para("Extranet text", 12, "B", 7)
    para(packet.get("extranet_text") or "", 9)
    for it in items:
        pdf.ln(3)
        para(f"Reservation {it['booking_ref']}: {it['label']}, {fmt(hotel, it['amount_minor'], it['currency'])}",
             11, "B", 6)
        para(item_sentence(hotel, it) + ".", 9)
        if it.get("clause_ref"):
            para(f"Clause {it['clause_ref']}" + (f": {it['clause_text']}" if it.get("clause_text") else ""), 9, "I")
        for s in it["calc"]:
            res = s.get("result_minor")
            val = fmt(hotel, res, it["currency"]) if isinstance(res, int) else (s.get("result") or "")
            inputs = ", ".join(f"{k}={v}" for k, v in (s.get("inputs") or {}).items())
            para(f"- {s.get('label')}: {s.get('formula')} ({inputs}) = {val}", 8)
        ev = [str(e.get("label") or e.get("kind")) for e in it["evidence"] if isinstance(e, dict)]
        if ev:
            para("Evidence: " + "; ".join(ev[:20]), 8)
    return bytes(pdf.output())


# ─── Create ───

DISPUTABLE = ("DETECTED", "ACCEPTED", "REJECTED", "UNRESOLVED")   # the last two: a second, better-argued packet


def get_packet(cur: Any, hotel_slug: str, packet_id: int, *, lock: bool = False) -> dict:
    cur.execute(f"SELECT * FROM pfg_dispute_packets WHERE hotel_slug = %s AND id = %s{' FOR UPDATE' if lock else ''}",
                (hotel_slug, packet_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Dispute packet not found", "packet_not_found")
    return dict(row)


def _deadline(items: list[dict]) -> datetime | None:
    dls = [it["deadline_at"] for it in items if it.get("deadline_at") is not None]
    return min(dls) if dls else None


def render_files(cur: Any, hotel: Any, packet: dict, items: list[dict] | None = None) -> dict:
    """(Re)generate the HTML and PDF files and the texts. Called on create
    and on approval (the stamp changes); the rows keep the latest ids."""
    items = items if items is not None else packet_items(cur, hotel, packet["finding_ids"])
    ext = extranet_text(hotel, packet["ota_code"], items, int(packet["total_minor"]), packet["currency"])
    subject, body = email_texts(hotel, packet["ota_code"], packet["id"], items, int(packet["total_minor"]),
                                packet["currency"])
    packet = {**packet, "extranet_text": ext}
    approver = approver_name(cur, packet)
    page = render_html(hotel, packet, items, approver=approver, links=evidence_links(hotel.slug, items))
    pdf = render_pdf(hotel, packet, items, approver=approver)
    name = f"dispute-{packet['ota_code'].lower()}-{packet['id']}"
    h = files.put_file(cur, hotel.slug, page.encode("utf-8"), filename=f"{name}.html", content_type="text/html",
                       purpose="profit.dispute_packet", source="generated", retention_days=RETENTION_DAYS)
    p = files.put_file(cur, hotel.slug, pdf, filename=f"{name}.pdf", content_type="application/pdf",
                       purpose="profit.dispute_packet", source="generated", retention_days=RETENTION_DAYS)
    cur.execute(
        "UPDATE pfg_dispute_packets SET extranet_text = %s, email_subject = %s, email_body = %s, html_file_id = %s, "
        "pdf_file_id = %s, updated_at = NOW() WHERE id = %s RETURNING *",
        (ext, subject, body, h.id, p.id, packet["id"]),
    )
    return dict(cur.fetchone())


def create_packet(cur: Any, hotel_slug: str, finding_ids: list[int], *, actor: Any = None,
                  note: str | None = None, adopt_action: dict | None = None) -> dict:
    """Group open RECOVERABLE findings of one OTA into a DRAFT packet,
    raise pg.dispute.ready_for_approval and propose one pg.dispute action.

    adopt_action: a pg.dispute a signal group already proposed (and a
    person approved from the brief): the packet is linked to it instead of
    proposing a second action, so the money is verified exactly once.
    Without it, undecided or open group proposals overlapping these
    findings are cancelled ("packet_built"): the packet's action takes
    over."""
    hotel = get_hotel(cur, hotel_slug)
    ids = sorted({int(i) for i in finding_ids or []})
    if not ids:
        raise ClefsError(400, "Pick at least one finding", "no_findings")
    if len(ids) > MAX_FINDINGS:
        raise ClefsError(400, f"At most {MAX_FINDINGS} findings per packet", "too_many_findings")
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s) AND NOT internal_only "
                "ORDER BY id FOR UPDATE", (hotel_slug, ids))
    rows = [dict(r) for r in cur.fetchall()]
    if len(rows) != len(ids):
        raise ClefsError(404, "Finding not found", "finding_not_found")
    if any(r["class"] != "RECOVERABLE" for r in rows):
        raise ClefsError(400, "Only recoverable findings can be disputed", "not_recoverable")
    if any(r["status"] not in DISPUTABLE for r in rows):
        raise ClefsError(409, "Only open, rejected or unresolved findings can be disputed", "finding_not_open")
    otas = {r["ota_code"] for r in rows}
    if len(otas) != 1 or None in otas:
        raise ClefsError(400, "A packet is for one OTA", "mixed_ota")
    ccys = {r["currency"] for r in rows}
    if len(ccys) != 1:
        raise ClefsError(400, "A packet is in one currency", "mixed_currency")
    held = [r["dispute_packet_id"] for r in rows if r["dispute_packet_id"]]
    if held:
        cur.execute("SELECT id FROM pfg_dispute_packets WHERE id = ANY(%s) AND status = ANY(%s)",
                    (held, list(OPEN_PACKET)))
        busy = [r["id"] for r in cur.fetchall()]
        if busy:
            raise ClefsError(409, f"Already in open packet {busy[0]}", "already_in_packet")
    ota, ccy = otas.pop(), ccys.pop()
    total = sum(int(r["amount_minor"]) - int(r["recovered_minor"] or 0) for r in rows)
    cur.execute(
        "INSERT INTO pfg_dispute_packets (hotel_slug, ota_code, finding_ids, total_minor, currency, created_by, "
        "outcome_note) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (hotel_slug, ota, ids, total, ccy, ledger._actor_uid(actor), (note or None) and note[:2000]),
    )
    packet = dict(cur.fetchone())
    cur.execute("UPDATE pfg_findings SET dispute_packet_id = %s, updated_at = NOW() WHERE id = ANY(%s)",
                (packet["id"], ids))
    items = packet_items(cur, hotel, ids)
    packet = render_files(cur, hotel, packet, items)
    deadline = _deadline(items)
    sig = pg_signals.raise_dispute_ready(cur, hotel, packet, deadline=deadline)
    if adopt_action is not None:
        act = {"id": adopt_action["id"]}
    else:
        _retire_group_proposals(cur, hotel_slug, ids, keep=None)
        act = actions.create_action(
            cur, hotel_slug, type=ACTION_TYPE, title=f"Dispute {OTA_NAMES.get(ota, ota)} packet {packet['id']}"[:200],
            params={"packet_id": packet["id"], "finding_ids": ids}, signal_id=sig["id"], expected_minor=total,
            currency=ccy, owner_role="owner", due_at=deadline, dedupe_key=f"pg:dispute:{packet['id']}", actor=actor,
            via="console" if actor is not None else "system")
    if act.get("id"):
        cur.execute("UPDATE pfg_dispute_packets SET action_id = %s WHERE id = %s RETURNING *",
                    (act["id"], packet["id"]))
        packet = dict(cur.fetchone())
        cur.execute("UPDATE pfg_findings SET action_id = %s WHERE id = ANY(%s)", (act["id"], ids))
    for r in rows:
        ledger.log_event(cur, r, r["status"], actor=actor, channel="packet",
                         note=f"Added to dispute packet {packet['id']}", amount_minor=int(r["amount_minor"]))
    if actor is not None and hasattr(actor, "hotel"):
        audit(cur, actor, "profit.dispute_create", "pfg_dispute_packet", packet["id"],
              after={"finding_ids": ids, "total_minor": total, "currency": ccy}, hotel_slug=hotel_slug)
    return packet


def _retire_group_proposals(cur: Any, hotel_slug: str, ids: list[int], *, keep: int | None) -> int:
    """Cancel live pg.dispute proposals raised from signal groups
    (dedupe_key "pg:<det>:...:action:<digest>") that touch these findings.
    Only states core.actions can cancel; one already done or submitted
    stays, and its verifier yields to the packet's action
    (actions._dispute_verify_findings)."""
    cur.execute(
        """
        SELECT id FROM actions
         WHERE hotel_slug = %s AND type = %s AND dedupe_key LIKE 'pg:%%:action:%%' AND status = ANY(%s)
           AND id IS DISTINCT FROM %s
           AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(params->'finding_ids', '[]'::jsonb)) e
                        WHERE e ~ '^[0-9]+$' AND e::BIGINT = ANY(%s))
        """,
        (hotel_slug, ACTION_TYPE, sorted(actions.CANCELLABLE), keep, ids),
    )
    n = 0
    for r in cur.fetchall():
        actions.cancel(cur, r["id"], actor=actions.SYSTEM, reason="packet_built")
        n += 1
    return n


# ─── Approve, submit, outcome ───


def can_approve(actor: Any) -> bool:
    """3.7: APPROVED needs the owner or finance role (a Clefs admin acting
    for the hotel counts as owner). A GM prepares packets, never signs them."""
    if actor is None or isinstance(actor, str) or not hasattr(actor, "role"):
        return False
    perms = getattr(actor, "permissions", frozenset()) or frozenset()
    return (getattr(actor, "role", None) in APPROVER_ROLES or bool(getattr(actor, "admin_bypass", False))
            or APPROVE_PERM in perms)


def _user_role(cur: Any, hotel_slug: str, user_id: int | None) -> str | None:
    if not user_id:
        return None
    cur.execute("SELECT role FROM hotel_memberships WHERE hotel_slug = %s AND user_id = %s AND is_active "
                "ORDER BY id LIMIT 1", (hotel_slug, user_id))
    row = cur.fetchone()
    return row["role"] if row else None


def _action_row(cur: Any, packet: dict) -> dict | None:
    if not packet.get("action_id"):
        return None
    cur.execute("SELECT * FROM actions WHERE id = %s AND hotel_slug = %s", (packet["action_id"], packet["hotel_slug"]))
    row = cur.fetchone()
    return dict(row) if row else None


def _mark_findings(cur: Any, hotel_slug: str, packet: dict, frm: tuple[str, ...], to: str, *, actor: Any,
                   note: str, evidence_for: Any = None) -> list[int]:
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s) AND status = ANY(%s) ORDER BY id",
                (hotel_slug, list(packet["finding_ids"]), list(frm)))
    moved = []
    for f in [dict(r) for r in cur.fetchall()]:
        ledger.transition(cur, hotel_slug, f["id"], to, actor=actor, note=note, channel="packet", refresh=False,
                          evidence=evidence_for(f) if evidence_for else None, force=True)
        moved.append(f["id"])
    if moved:
        pg_signals.refresh_findings_signals(cur, hotel_slug, moved)
    return moved


def approve(cur: Any, hotel_slug: str, packet_id: int, *, actor: Any, via: str = "console") -> dict:
    """DRAFT -> APPROVED: stamp the approver, move the findings to ACCEPTED,
    approve the pg.dispute action on the dispute path and re-render the
    packet with the stamp."""
    if not can_approve(actor):
        raise ClefsError(403, "Only the owner or finance can approve a dispute", "approval_role_required")
    hotel = get_hotel(cur, hotel_slug)
    packet = get_packet(cur, hotel_slug, packet_id, lock=True)
    if packet["status"] != "DRAFT":
        raise ClefsError(409, f"A {packet['status']} packet cannot be approved", "illegal_transition")
    uid = ledger._actor_uid(actor)
    cur.execute("UPDATE pfg_dispute_packets SET status = 'APPROVED', approved_by = %s, approved_at = %s, "
                "updated_at = NOW() WHERE id = %s RETURNING *", (uid, clock.now(), packet_id))
    packet = dict(cur.fetchone())
    _mark_findings(cur, hotel_slug, packet, ("DETECTED",), "ACCEPTED", actor=actor,
                   note=f"Dispute packet {packet_id} approved")
    act = _action_row(cur, packet)
    if act is not None and act["status"] in ("proposed", "snoozed"):
        actions.decide(cur, act["id"], "dispute", actor=actor, via=via, hotel_slug=hotel_slug,
                       note=f"packet {packet_id} approved")
    pg_signals.resolve_dispute(cur, hotel_slug, packet_id, resolution="approved")
    packet = render_files(cur, hotel, packet)
    audit(cur, actor, "profit.dispute_approve", "pfg_dispute_packet", packet_id, hotel_slug=hotel_slug,
          after={"status": "APPROVED"})
    return packet


def submitted(cur: Any, hotel_slug: str, packet_id: int, *, actor: Any, channel: str = "extranet",
              reference: str | None = None, note: str | None = None, send_to: str | None = None) -> dict:
    """APPROVED -> SUBMITTED: the packet went to the OTA (pasted in the
    extranet, or emailed from here with `send_to`). The findings become
    DISPUTED with a snapshot of what the OTA invoices per booking now, and
    the follow-up clock starts (10 business days)."""
    hotel = get_hotel(cur, hotel_slug)
    packet = get_packet(cur, hotel_slug, packet_id, lock=True)
    if packet["status"] != "APPROVED":
        raise ClefsError(409, "Approve the packet before submitting it", "not_approved")
    now = clock.now()
    if send_to:
        from clefs import notify

        page = render_html(hotel, packet, packet_items(cur, hotel, packet["finding_ids"]),
                           approver=approver_name(cur, packet))
        notify.send_email(send_to, packet["email_subject"] or f"Dispute {packet_id}", page,
                          text=packet["email_body"], attachments=[str(packet["pdf_file_id"])] if
                          packet.get("pdf_file_id") else [], hotel_slug=hotel_slug, purpose="profit.dispute",
                          idempotency_key=f"pg:dispute:{packet_id}:email", action_id=packet.get("action_id"), cur=cur)
        channel = "email"
    follow = htime_end(hotel, add_business_days(hotel, htime.local_date(hotel, now), FOLLOW_UP_BUSINESS_DAYS))
    cur.execute("UPDATE pfg_dispute_packets SET status = 'SUBMITTED', submitted_at = %s, follow_up_at = %s, "
                "outcome_note = COALESCE(%s, outcome_note), updated_at = NOW() WHERE id = %s RETURNING *",
                (now, follow, (note or None) and note[:2000], packet_id))
    packet = dict(cur.fetchone())

    def snapshot(f: dict) -> list:
        norm = ledger.res_norm_of(f)
        inv = ledger.invoiced_commission(cur, hotel_slug, f["ota_code"], norm, f["currency"]) if norm else None
        ev = [{"kind": "calc", "label": ledger.INVOICED_LABEL, "value": inv}] if inv is not None else []
        return ev + [{"kind": "link", "label": f"submitted via {channel}", "value": reference}]

    _mark_findings(cur, hotel_slug, packet, ("DETECTED", "ACCEPTED", "REJECTED", "UNRESOLVED"), "DISPUTED",
                   actor=actor, note=f"Packet {packet_id} submitted via {channel}"
                   + (f" (ref {reference})" if reference else ""), evidence_for=snapshot)
    act = _action_row(cur, packet)
    if act is not None and act["status"] in ("approved", "rejected_by_counterparty") and act.get("subtype") == "dispute":
        actions.submit(cur, act["id"], actor=actor, data={"channel": channel, "reference": reference})
        actions.await_counterparty(cur, act["id"], actor=actor)
    audit(cur, actor, "profit.dispute_submit", "pfg_dispute_packet", packet_id, hotel_slug=hotel_slug,
          after={"status": "SUBMITTED", "channel": channel, "reference": reference})
    return packet


def htime_end(hotel: Any, d: date) -> datetime:
    """The end of hotel-local day `d` (the next midnight)."""
    return datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=hotel.tz)


def add_business_days(hotel: Any, d: date, n: int) -> date:
    out, left = d, n
    while left > 0:
        out += timedelta(days=1)
        if not htime.is_weekend(hotel, out):
            left -= 1
    return out


OUTCOMES = ("ACKNOWLEDGED", "CREDITED", "PARTIAL", "REJECTED")


def outcome(cur: Any, hotel_slug: str, packet_id: int, *, result: str, actor: Any,
            amount_minor: int | None = None, note: str | None = None) -> dict:
    """What the OTA answered, recorded by a person. CREDITED recovers every
    finding in full; PARTIAL spreads `amount_minor` over them in deadline
    order (95% of a finding still counts as recovered); REJECTED rejects
    them. The statement-based auto-verification can confirm it later."""
    result = (result or "").upper()
    if result not in OUTCOMES:
        raise ClefsError(400, f"outcome must be one of {', '.join(OUTCOMES)}", "invalid_outcome")
    hotel = get_hotel(cur, hotel_slug)
    packet = get_packet(cur, hotel_slug, packet_id, lock=True)
    if packet["status"] not in ("SUBMITTED", "ACKNOWLEDGED", "PARTIAL"):
        raise ClefsError(409, f"A {packet['status']} packet has no outcome yet", "illegal_transition")
    if result == "ACKNOWLEDGED":
        cur.execute("UPDATE pfg_dispute_packets SET status = 'ACKNOWLEDGED', outcome_note = COALESCE(%s, outcome_note), "
                    "updated_at = NOW() WHERE id = %s RETURNING *", ((note or None) and note[:2000], packet_id))
        return dict(cur.fetchone())
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s) ORDER BY deadline_at NULLS LAST, id",
                (hotel_slug, list(packet["finding_ids"])))
    rows = [dict(r) for r in cur.fetchall() if r["status"] not in ("RECOVERED", "DISMISSED")]
    got = {"source": "MANUAL", "lines": [{"kind": "quote", "label": "recorded outcome", "value": note or result}]}
    moved: list[int] = []
    if result == "CREDITED":
        for f in rows:
            ledger.transition(cur, hotel_slug, f["id"], "RECOVERED", actor=actor, channel="packet", note=note,
                              verified_by="MANUAL", refresh=False, force=True, evidence=got["lines"])
            moved.append(f["id"])
    elif result == "PARTIAL":
        if not amount_minor or amount_minor <= 0:
            raise ClefsError(400, "A partial credit needs its amount", "amount_required")
        left, tally = int(amount_minor), {"recovered": [], "partial": []}
        for f in rows:
            take = min(left, int(f["amount_minor"]) - int(f["recovered_minor"] or 0))
            if take <= 0:
                continue
            left -= take
            if f["status"] != "DISPUTED":
                ledger.transition(cur, hotel_slug, f["id"], "DISPUTED", actor=actor, channel="packet", refresh=False,
                                  force=True, note="Partial credit recorded")
                f = ledger._load(cur, hotel_slug, f["id"])
            ledger._apply_credit(cur, hotel, f, take, got, tally)
            moved.append(f["id"])
        if moved:
            pg_signals.raise_recovery_verified(cur, hotel, packet["ota_code"], htime.local_date(hotel, clock.now()))
    else:
        for f in rows:
            ledger.transition(cur, hotel_slug, f["id"], "REJECTED", actor=actor, channel="packet", note=note,
                              refresh=False, force=True)
            moved.append(f["id"])
    cur.execute("UPDATE pfg_dispute_packets SET outcome_note = COALESCE(%s, outcome_note), updated_at = NOW() "
                "WHERE id = %s", ((note or None) and note[:2000], packet_id))
    if moved:
        pg_signals.refresh_findings_signals(cur, hotel_slug, moved)
    sync_packets_for(cur, hotel, moved or list(packet["finding_ids"]))
    audit(cur, actor, "profit.dispute_outcome", "pfg_dispute_packet", packet_id, hotel_slug=hotel_slug,
          after={"outcome": result, "amount_minor": amount_minor})
    return get_packet(cur, hotel_slug, packet_id)


# ─── Sync with the ledger and the action ───

VERIFYING = ("submitted", "awaiting_counterparty", "done")
UNSENT = ("proposed", "snoozed", "approved", "assigned")


def packet_state(rows: list[dict]) -> str | None:
    """The packet status its findings imply, or None (no change)."""
    live = [r for r in rows if r["status"] != "DISMISSED"]
    if not live:
        return None
    if all(r["status"] == "RECOVERED" for r in live):
        return "CREDITED"
    if any(int(r["recovered_minor"] or 0) > 0 for r in live):
        return "PARTIAL"
    if all(r["status"] in ("REJECTED", "UNRESOLVED") for r in live):
        return "REJECTED"
    return None


def _outcome_minor(rows: list[dict]) -> int:
    return sum(int(r["recovered_minor"] or 0) for r in rows if r["status"] != "DISMISSED")


def verify_result(packet: dict, rows: list[dict], *, now: datetime) -> actions.VerifyResult:
    """The pg.dispute verifier's verdict from the packet's findings:
    everything credited -> verified; a partial credit -> verified with the
    partial amount once no new credit came for 30 days (the OTA is done);
    rejected or unresolved -> not verified; otherwise pending."""
    state = packet_state(rows)
    ev = [{"kind": "row", "label": f"Dispute packet {packet['id']}",
           "ref": {"table": "pfg_dispute_packets", "id": packet["id"]}}]
    got = _outcome_minor(rows)
    if state == "CREDITED" or (state == "PARTIAL" and now - (packet.get("updated_at") or now) >= timedelta(days=30)):
        return actions.VerifyResult(status="verified", outcome_minor=got, currency=packet["currency"],
                                    attribution="caused", evidence=ev, confidence=0.95)
    if state == "REJECTED":
        return actions.VerifyResult(status="not_verified", outcome_minor=0, currency=packet["currency"], evidence=ev)
    return actions.VerifyResult(status="pending", evidence=ev)


def _sync_action(cur: Any, hotel: Any, packet: dict, rows: list[dict], state: str) -> None:
    act = _action_row(cur, packet)
    if act is None:
        return
    if state == "CREDITED" and act["status"] in UNSENT:
        # The OTA credited before the packet went out: nothing to verify on
        # this action; the finding's own ledger already holds the money.
        actions.cancel(cur, act["id"], actor=actions.SYSTEM, reason="credited_before_submission")
        return
    if state in ("CREDITED", "REJECTED") and act["status"] in VERIFYING:
        actions.record_verification(cur, act["id"], result=verify_result(packet, rows, now=clock.now()),
                                    actor=actions.SYSTEM)
        act = _action_row(cur, packet) or act
    if state == "REJECTED" and act["status"] in ("rejected_by_counterparty", "awaiting_counterparty") \
            and all(r["status"] == "UNRESOLVED" for r in rows if r["status"] != "DISMISSED"):
        actions.give_up(cur, act["id"], actor=actions.SYSTEM, note="no credit after the dispute window")


def sync_packets_for(cur: Any, hotel: Any, finding_ids: list[int]) -> list[int]:
    """Bring each packet holding these findings (and its action) in line
    with the ledger. Returns the packet ids whose status changed."""
    if not finding_ids:
        return []
    cur.execute("SELECT DISTINCT dispute_packet_id FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s) "
                "AND dispute_packet_id IS NOT NULL", (hotel.slug, list(finding_ids)))
    changed = []
    for pid in [r["dispute_packet_id"] for r in cur.fetchall()]:
        packet = get_packet(cur, hotel.slug, pid, lock=True)
        cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s)",
                    (hotel.slug, list(packet["finding_ids"])))
        rows = [dict(r) for r in cur.fetchall()]
        state = packet_state(rows)
        if state is None or state == packet["status"]:
            continue
        cur.execute("UPDATE pfg_dispute_packets SET status = %s, updated_at = NOW() WHERE id = %s RETURNING *",
                    (state, pid))
        packet = dict(cur.fetchone())
        changed.append(pid)
        _sync_action(cur, hotel, packet, rows, state)
        if state in ("CREDITED", "REJECTED"):
            pg_signals.resolve_dispute(cur, hotel.slug, pid, approval=True, follow_up=True,
                                       resolution=state.lower())
    return changed


# ─── Follow-up job and the brief's door ───


def adopt_action_approval(cur: Any, hotel: Any) -> list[int]:
    """A packet whose pg.dispute action an owner or finance user approved
    elsewhere (the brief, WhatsApp) is APPROVED here too, stamped with that
    user. An approval by anyone else leaves the packet a DRAFT."""
    cur.execute(
        """
        SELECT p.id, a.decided_by, a.decided_at FROM pfg_dispute_packets p JOIN actions a ON a.id = p.action_id
         WHERE p.hotel_slug = %s AND p.status = 'DRAFT' AND a.status IN ('approved', 'assigned', 'in_progress')
        """,
        (hotel.slug,),
    )
    adopted = []
    for r in [dict(x) for x in cur.fetchall()]:
        if _user_role(cur, hotel.slug, r["decided_by"]) not in APPROVER_ROLES:
            continue
        cur.execute("UPDATE pfg_dispute_packets SET status = 'APPROVED', approved_by = %s, approved_at = %s, "
                    "updated_at = NOW() WHERE id = %s RETURNING *", (r["decided_by"], r["decided_at"], r["id"]))
        packet = dict(cur.fetchone())
        _mark_findings(cur, hotel.slug, packet, ("DETECTED",), "ACCEPTED", actor="brief",
                       note=f"Dispute packet {r['id']} approved from the brief")
        render_files(cur, hotel, packet)
        pg_signals.resolve_dispute(cur, hotel.slug, r["id"], resolution="approved")
        adopted.append(r["id"])
    return adopted


def follow_up(cur: Any, hotel: Any, *, now: datetime | None = None) -> dict:
    """pg.dispute_followup (daily): every submitted packet still without a
    full credit whose follow-up date passed gets a reminder signal, and the
    next date moves 10 business days on."""
    now = now or clock.now()
    adopted = adopt_action_approval(cur, hotel)
    cur.execute("SELECT * FROM pfg_dispute_packets WHERE hotel_slug = %s AND status IN ('SUBMITTED', 'ACKNOWLEDGED', "
                "'PARTIAL') AND follow_up_at IS NOT NULL AND follow_up_at <= %s ORDER BY id", (hotel.slug, now))
    reminded = []
    for p in [dict(r) for r in cur.fetchall()]:
        cur.execute("SELECT COALESCE(SUM(recovered_minor), 0) AS s FROM pfg_findings WHERE id = ANY(%s)",
                    (list(p["finding_ids"]),))
        p["recovered_minor"] = int(cur.fetchone()["s"])
        days_open = (now - (p["submitted_at"] or now)).days
        pg_signals.raise_dispute_follow_up(cur, hotel, p, days_open=days_open)
        nxt = htime_end(hotel, add_business_days(hotel, htime.local_date(hotel, now), FOLLOW_UP_BUSINESS_DAYS))
        cur.execute("UPDATE pfg_dispute_packets SET follow_up_at = %s, updated_at = updated_at WHERE id = %s",
                    (nxt, p["id"]))
        act = _action_row(cur, p)
        if act is not None and act["status"] not in ("verified", "not_verified", "unresolved", "cancelled"):
            try:
                actions.decide(cur, act["id"], "remind", actor=actions.SYSTEM, via="system", hotel_slug=hotel.slug)
            except ClefsError:
                pass  # a state the reminder does not apply to; the signal is the reminder
        reminded.append(p["id"])
    return {"reminded": reminded, "adopted": adopted}


def _packet_for_proposal(cur: Any, hotel_slug: str, action_id: int) -> dict | None:
    """A pg.dispute proposed from a signal group has no packet until the
    brief's Dispute verb asks for one: build it now from the action's
    findings and link it to that action (no second action). None when the
    action is not such a proposal or its findings can no longer be
    disputed (already in another packet, credited, dismissed)."""
    cur.execute("SELECT * FROM actions WHERE id = %s AND hotel_slug = %s", (action_id, hotel_slug))
    act = cur.fetchone()
    if act is None or act["type"] != ACTION_TYPE or (act["params"] or {}).get("packet_id") \
            or act["status"] in actions.TERMINAL:
        return None
    ids = [int(i) for i in (act["params"] or {}).get("finding_ids") or [] if str(i).isdigit()]
    if not ids:
        return None
    cur.execute("SAVEPOINT pfg_adopt_packet")
    try:
        packet = create_packet(cur, hotel_slug, ids, actor=None, adopt_action=dict(act),
                               note="Built from the brief's Dispute")
    except ClefsError:
        cur.execute("ROLLBACK TO SAVEPOINT pfg_adopt_packet")
        return None
    cur.execute("RELEASE SAVEPOINT pfg_adopt_packet")
    adopt_action_approval(cur, get_hotel(cur, hotel_slug))
    return get_packet(cur, hotel_slug, packet["id"])


def packet_for_action(cur: Any, hotel_slug: str, *, action_id: int) -> dict | None:
    """The brief's door (brief/whatsapp.py looks up profit_guard.api.
    dispute_packet): the packet PDF of a pg.dispute action, as {file_id,
    filename}; None when the action has no packet."""
    cur.execute("SELECT * FROM pfg_dispute_packets WHERE hotel_slug = %s AND action_id = %s ORDER BY id DESC LIMIT 1",
                (hotel_slug, action_id))
    row = cur.fetchone()
    packet = dict(row) if row is not None else _packet_for_proposal(cur, hotel_slug, action_id)
    if packet is None:
        return None
    if not packet.get("pdf_file_id"):
        packet = render_files(cur, get_hotel(cur, hotel_slug), packet)
    return {"file_id": str(packet["pdf_file_id"]), "filename": f"dispute-{packet['ota_code'].lower()}-{packet['id']}.pdf",
            "packet_id": packet["id"]}


# ─── Routes ───
# Mounted by the manifest (FOUNDATION-REQUEST: module.py lists
# disputes.router). Reads need profit.view; create, submit and outcome
# profit.manage; approve additionally the owner or finance role.

from fastapi import APIRouter, Depends, Query, Response  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from clefs.auth.deps import HotelCtx, require_perm  # noqa: E402
from clefs.core import pii  # noqa: E402

router = APIRouter(prefix="/hotels/{slug}/profit-guard", tags=["profit_guard"])
VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")
MASK = {"items.guest_name": "guest_name"}


class PacketIn(BaseModel):
    finding_ids: list[int] = Field(min_length=1, max_length=MAX_FINDINGS)
    note: str | None = Field(default=None, max_length=2000)


class SubmittedIn(BaseModel):
    channel: str = Field(default="extranet", pattern="^(extranet|email|portal|other)$")
    reference: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    send_to: str | None = Field(default=None, max_length=320)   # email it from here to this address


class OutcomeIn(BaseModel):
    outcome: str = Field(max_length=20)
    amount: Decimal | None = Field(default=None, gt=0)          # PARTIAL: the credited amount
    note: str | None = Field(default=None, max_length=2000)


def packet_out(cur: Any, hotel: Any, packet: dict, *, ctx: Any = None, with_items: bool = True) -> dict:
    from clefs.modules.profit_guard import repo

    out = repo.out(packet, money_fields=("total_minor",))
    out["finding_ids"] = list(packet["finding_ids"] or [])
    out["approved_by_name"] = approver_name(cur, packet)
    out["can_approve"] = can_approve(ctx) and packet["status"] == "DRAFT"
    out["pdf_path"] = f"/hotels/{hotel.slug}/profit-guard/disputes/{packet['id']}.pdf"
    out["print_path"] = f"/hotels/{hotel.slug}/profit-guard/disputes/{packet['id']}/print"
    if with_items:
        items = packet_items(cur, hotel, packet["finding_ids"])
        out["recovered_minor"] = sum(it["recovered_minor"] for it in items)
        out["recovered"] = money.money_str(out["recovered_minor"], packet["currency"])
        out["items"] = [{**{k: v for k, v in it.items() if k != "evidence"},
                         "amount": money.money_str(it["amount_minor"], it["currency"]), "evidence": it["evidence"]}
                        for it in items]
        if ctx is not None:
            out = pii.mask_for_ctx(out, ctx, field_classes=MASK)
    return out


@router.get("/disputes")
def list_packets(slug: str, status: str | None = None, limit: int = Query(100, ge=1, le=500),
                 ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    wanted = [s.strip().upper() for s in (status or "").split(",") if s.strip()] or None
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        cur.execute("SELECT * FROM pfg_dispute_packets WHERE hotel_slug = %s AND (%s::text[] IS NULL OR status = ANY(%s)) "
                    "ORDER BY created_at DESC, id DESC LIMIT %s", (slug, wanted, wanted, limit))
        return [packet_out(cur, hotel, dict(r), ctx=ctx, with_items=False) for r in cur.fetchall()]


@router.post("/disputes", status_code=201)
def post_packet(slug: str, body: PacketIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        packet = create_packet(cur, slug, body.finding_ids, actor=ctx, note=body.note)
        return packet_out(cur, get_hotel(cur, slug), packet, ctx=ctx)


# The .pdf route comes before /disputes/{id}: both would otherwise match "7.pdf".
@router.get("/disputes/{packet_id:int}.pdf")
def get_packet_pdf(slug: str, packet_id: int, ctx: HotelCtx = Depends(VIEW)) -> Response:
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        packet = get_packet(cur, slug, packet_id)
        items = packet_items(cur, hotel, packet["finding_ids"])
        pdf = render_pdf(hotel, packet, items, approver=approver_name(cur, packet))
    name = f"dispute-{packet['ota_code'].lower()}-{packet_id}.pdf"
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/disputes/{packet_id:int}/print", response_class=HTMLResponse)
def get_packet_print(slug: str, packet_id: int, ctx: HotelCtx = Depends(VIEW)) -> HTMLResponse:
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        packet = get_packet(cur, slug, packet_id)
        items = packet_items(cur, hotel, packet["finding_ids"])
        page = render_html(hotel, packet, items, approver=approver_name(cur, packet),
                           links=evidence_links(slug, items))
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/disputes/{packet_id:int}")
def get_packet_route(slug: str, packet_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return packet_out(cur, get_hotel(cur, slug), get_packet(cur, slug, packet_id), ctx=ctx)


@router.post("/disputes/{packet_id:int}/approve")
def post_approve(slug: str, packet_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        packet = approve(cur, slug, packet_id, actor=ctx)
        return packet_out(cur, get_hotel(cur, slug), packet, ctx=ctx)


@router.post("/disputes/{packet_id:int}/submitted")
def post_submitted(slug: str, packet_id: int, body: SubmittedIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        packet = submitted(cur, slug, packet_id, actor=ctx, channel=body.channel, reference=body.reference,
                           note=body.note, send_to=body.send_to)
        return packet_out(cur, get_hotel(cur, slug), packet, ctx=ctx)


@router.post("/disputes/{packet_id:int}/outcome")
def post_outcome(slug: str, packet_id: int, body: OutcomeIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        packet = get_packet(cur, slug, packet_id)
        amount = money.to_minor(body.amount, packet["currency"]) if body.amount is not None else None
        packet = outcome(cur, slug, packet_id, result=body.outcome, actor=ctx, amount_minor=amount, note=body.note)
        return packet_out(cur, get_hotel(cur, slug), packet, ctx=ctx)
