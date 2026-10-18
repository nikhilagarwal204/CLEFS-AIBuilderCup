"""Inbound email: statements forwarded to <alias>+ingest[-<hint>]@in.clefs.world
become import batches (02 spec 2.2 step 7; 04 spec F4 address grammar).

The webhook itself (POST /webhooks/email/resend: Svix signature,
INBOUND_EMAIL_ENABLED, MessageID dedupe, attachments stored as files) is
foundation code (clefs.notify). This handler only turns the stored
attachments into batches and queues their processing, so Resend gets its
200 before any AI call runs.

The optional <hint> after "ingest-" names a source ("booking_res_statement")
or a kind ("pms_reservations" -> PMS_RESERVATIONS). Without one, table files
are recognised by their headers and PDFs from an OTA's domain are taken as
that OTA's statement.

Auto-commit is earned per message, not granted by the address: see
"Sender trust" below (GET|PUT /hotels/{slug}/ingest/email-senders keeps
the owner's extra trusted senders).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clefs import jobs, notify
from clefs.core import hotels
from clefs.core.errors import ClefsError
from clefs.core.ingest import aliases, batches
from clefs.core.ingest.kinds import KINDS
from clefs.core.ingest.registrations import PROCESS_TASK

log = logging.getLogger("clefs.ingest")

# Sender domains whose PDFs are OTA statements (UNVERIFIED: the sending
# domains OTAs use for finance mail; extend as real mail arrives).
OTA_DOMAINS = {"booking.com": "BOOKING", "agoda.com": "AGODA", "expediagroup.com": "EXPEDIA",
               "expedia.com": "EXPEDIA"}
IMPORTABLE_TYPES = ("csv", "sheet", "excel", "pdf", "calendar", "text/plain", "octet-stream", "xml")


def hint_labels(ref: str | None) -> tuple[str | None, str | None]:
    """(kind, source_code) from the address hint."""
    if not ref:
        return None, None
    r = ref.strip().lower()
    if aliases.get_map(r) is not None and r != aliases.GENERIC:
        return aliases.source_kind(r), r
    k = r.upper()
    return (k, None) if k in KINDS else (None, None)


def sender_ota(sender: str | None) -> str | None:
    domain = (sender or "").rsplit("@", 1)[-1].lower().strip(">")
    return next((ota for d, ota in OTA_DOMAINS.items() if domain == d or domain.endswith("." + d)), None)


# ─── Sender trust (who may auto-commit) ───
# The alias is chosen by the admin, so it is guessable, and anyone can mail
# it. A batch from mail is always created (a person can look at it), but it
# commits without a person only when the From address is both proven (SPF,
# DKIM or DMARC pass, aligned with the From domain) and vouched for (an
# active member of the hotel, or on the owner's ingest sender list).
# Otherwise it waits in REVIEW, so a stranger cannot plant or supersede
# reservations, occupancy or P&L data by mimicking the PMS export.

SETTINGS_MODULE = "ingest"  # hotel_module_settings row that holds email_senders
_ADDR = re.compile(r"[\w.+'-]+@([\w-]+(?:\.[\w-]+)+)")


def _domain(addr: str | None) -> str | None:
    m = _ADDR.search(addr or "")
    return m.group(1).lower().rstrip(".") if m else None


def _aligned(domain: str | None, from_domain: str) -> bool:
    """Relaxed DMARC alignment: the authenticated domain is the From domain
    or a parent of it (mail.hotel.ae signs for hotel.ae and back)."""
    if not domain:
        return False
    d = domain.lower().rstrip(".")
    return d == from_domain or from_domain.endswith("." + d) or d.endswith("." + from_domain)


def _header(msg: Any, name: str) -> str:
    want = name.lower()
    return next((str(v) for k, v in (getattr(msg, "headers", None) or {}).items() if k.lower() == want), "")


def sender_authenticated(msg: Any) -> bool:
    """True when the From domain is proven by the receiving server: a DMARC
    pass, or an SPF or DKIM pass whose domain aligns with From. A bare pass
    for some other domain proves nothing about From (anyone can sign mail
    for their own domain and write From: owner@hotel.ae).

    Only verdicts our own verifier wrote are read: InboundEmail.headers comes
    from notify.inbound.trusted_headers, which drops any Authentication-Results
    or Received-SPF that the sender could have written (another authserv-id or
    receiver, or a second copy beside the real stamp). A message without a
    trusted verdict never auto-commits; its batches wait in REVIEW."""
    from_domain = _domain(msg.sender)
    if from_domain is None:
        return False
    results = _header(msg, "Authentication-Results").lower()
    if re.search(r"\bdmarc=pass\b", results):
        dm = re.search(r"\bdmarc=pass\b[^;]*?header\.from=([\w.-]+)", results)
        if dm is None or _aligned(dm.group(1), from_domain):
            return True
    if getattr(msg, "dkim", None) == "pass":
        for d in re.findall(r"\bdkim=pass\b[^;]*?header\.(?:d|i)=@?([\w.-]+)", results):
            if _aligned(d, from_domain):
                return True
    if getattr(msg, "spf", None) == "pass":
        spf = _header(msg, "Received-SPF")
        env = re.search(r"envelope-from=\"?<?([^\s\";>]+)", spf) or re.search(r"smtp\.mailfrom=([^\s;]+)", results)
        if env is not None and _aligned(_domain(env.group(1)) or env.group(1).lstrip("@"), from_domain):
            return True
    return False


def email_senders(cur: Any, hotel_slug: str) -> list[str]:
    """The owner's extra trusted senders: full addresses or "@domain"."""
    row = hotels.module_settings_row(cur, hotel_slug, SETTINGS_MODULE)
    raw = ((row or {}).get("settings") or {}).get("email_senders") or []
    return [str(e).strip().lower() for e in raw if isinstance(e, str) and e.strip()]


def sender_vouched(cur: Any, hotel_slug: str, sender: str | None) -> bool:
    m = _ADDR.search(sender or "")
    if m is None:
        return False
    addr, domain = m.group(0).lower(), m.group(1).lower()
    for e in email_senders(cur, hotel_slug):
        if addr == e or (e.startswith("@") and domain == e[1:]):
            return True
    cur.execute(
        """SELECT 1 FROM users u JOIN hotel_memberships m ON m.user_id = u.id
           WHERE m.hotel_slug = %s AND m.is_active AND u.is_active AND lower(u.email) = %s LIMIT 1""",
        (hotel_slug, addr),
    )
    return cur.fetchone() is not None


def may_auto_commit(msg: Any) -> bool:
    """Both halves: a proven From address the hotel vouches for."""
    return sender_authenticated(msg) and sender_vouched(msg.cur, msg.hotel_slug, msg.sender)


def _importable(ref: Any) -> bool:
    ctype = (getattr(ref, "content_type", "") or "").lower()
    name = (getattr(ref, "filename", "") or "").lower()
    return any(t in ctype for t in IMPORTABLE_TYPES) or name.endswith((".csv", ".xlsx", ".xls", ".pdf", ".ics",
                                                                       ".txt", ".tsv", ".xml"))


@notify.on_inbound_email("ingest", priority=50)
def on_ingest_email(msg: Any) -> dict:
    """One batch per attachment; each queued for ingest.process. Handled
    when at least one batch was created (or already existed). Batches from
    an unproven or unknown sender wait in REVIEW for a person."""
    cur = msg.cur
    kind_hint, source_hint = hint_labels(msg.ref)
    ota = sender_ota(msg.sender)
    auto = may_auto_commit(msg)
    if not auto:
        log.info("ingest email %s from an untrusted sender: batches wait for review", msg.inbound_id)
    created, existing, skipped = [], [], []
    for ref in msg.attachments or []:
        if not _importable(ref):
            skipped.append({"filename": ref.filename, "reason": "not a statement file"})
            continue
        kind = kind_hint
        if kind is None and ota and (ref.filename or "").lower().endswith(".pdf"):
            kind = "OTA_STATEMENT"
        cur.execute("SAVEPOINT clefs_ingest_email")
        try:
            b = batches.create_batch(
                cur, msg.hotel_slug, file_id=str(ref.id), kind=kind, source_code=source_hint, ota_code=ota,
                via="EMAIL", inbound_message_id=msg.inbound_id, auto_commit=auto, process=False,
            )
            cur.execute("RELEASE SAVEPOINT clefs_ingest_email")
        except ClefsError as exc:
            cur.execute("ROLLBACK TO SAVEPOINT clefs_ingest_email")
            cur.execute("RELEASE SAVEPOINT clefs_ingest_email")
            skipped.append({"filename": ref.filename, "reason": exc.code})
            continue
        if not b["created"]:
            existing.append(b["id"])
            continue
        jobs.enqueue(PROCESS_TASK, {"batch_id": b["id"]}, hotel_slug=msg.hotel_slug, cur=cur,
                     dedupe_key=f"{PROCESS_TASK}:{b['id']}")
        created.append(b["id"])
    if skipped:
        log.info("ingest email %s skipped %s", msg.inbound_id, skipped)
    return {"handled": bool(created or existing), "batches": created, "existing": existing, "skipped": skipped}
