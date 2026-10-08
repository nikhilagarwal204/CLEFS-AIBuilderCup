"""
Data subject requests (03 spec 5.3, UAE PDPL Arts. 13-18): intake from the
public form or the console, a queue with a due date, identity verification,
and executors:

  access / portability  a JSON + CSV bundle of what the hotel holds on the
                        person (profiles, stays, consents, their own
                        transcripts and requests, messages, orders)
  erase                 operational rows anonymized ("[erased]"), profile and
                        contact rows deleted, every consent withdrawn
  restrict              a flag (trs_subject_flags) plus withdrawal of the
                        marketing (every marketing_* channel purpose too)
                        and ai_analytics consents, and a guests marketing
                        suppression for each contact given
  object                the same for marketing, ai_analytics, profile_memory
  rectify               done by staff in the owning screens, then closed here

WHY the requester's details are sealed: the queue is itself personal data
(and often includes a passport photo reference or a phone number). The row
keeps privacy.seal(json) plus contact_hmac hex values for matching; SQL never
sees a raw identifier, and only the console (trust.privacy.manage) unseals.

Due date: 30 days from receipt. The PDPL leaves response deadlines to its
executive regulations, which are unverified (research/uae/privacy-reviews.md),
so 30 days is the conservative GDPR-equivalent default.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from clefs import files, jobs
from clefs.audit import audit
from clefs.core import clock, consent, pii, privacy, signals
from clefs.core.hotels import get_hotel
from clefs.modules.trust import subject as subject_mod
from clefs.core.errors import ClefsError, conflict, not_found
from clefs.db import Json

TYPES = ("access", "rectify", "erase", "restrict", "object", "portability")
OPEN_STATUSES = ("received", "verified", "in_progress")
CHANNELS = ("public_form", "console", "email", "whatsapp", "phone", "in_person")
VERIFY_METHODS = ("id_document", "matched_reservation", "email_reply", "phone_callback", "in_person")
DUE_DAYS = 30
ERASED = "[erased]"

# ─── Intake ───


class Identifiers(BaseModel):
    """What the requester gave to identify themselves. At least one contact
    (email or phone) or a reservation reference is required."""

    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    room: str | None = Field(default=None, max_length=20)
    reservation_ref: str | None = Field(default=None, max_length=80)
    stay_date: str | None = Field(default=None, max_length=10)  # YYYY-MM-DD, any night of the stay


def _hmacs(cur: Any, hotel_slug: str, ids: Identifiers) -> list[str]:
    out = []
    for kind, raw in (("email", ids.email), ("phone", ids.phone)):
        if not raw:
            continue
        try:
            out.append(privacy.contact_hmac(hotel_slug, kind, raw, cur=cur).hex())
        except ClefsError:
            # A malformed number still files the request; staff see the raw
            # value (sealed) and verify by other means.
            continue
    return out


def _hint(ids: Identifiers) -> dict:
    return {
        "name": pii.first_name(ids.name) if ids.name else None,
        "email": privacy.mask_email(ids.email) if ids.email else None,
        "phone": pii.mask_contact_phone(ids.phone) if ids.phone else None,
        "room": ids.room,
        "has_reservation_ref": bool(ids.reservation_ref),
    }


def create_request(
    cur: Any,
    hotel_slug: str,
    *,
    type: str,
    identifiers: Identifiers,
    details: str | None,
    channel: str,
    language: str = "en",
    actor: Any = None,
    suspected_flood: bool = False,
) -> dict:
    """File one request. suspected_flood (public form only): it arrived
    while the hotel's form was over its daily threshold. It is filed all the
    same (the form is a legal channel), but it shares one aggregate signal
    instead of raising its own, so junk cannot bury the owner's brief."""
    if type not in TYPES:
        raise ClefsError(400, f"type must be one of {', '.join(TYPES)}", "invalid_dsr_type")
    if channel not in CHANNELS:
        raise ClefsError(400, f"channel must be one of {', '.join(CHANNELS)}", "invalid_channel")
    if not (identifiers.email or identifiers.phone or identifiers.reservation_ref):
        raise ClefsError(400, "Give an email address, a phone number or a booking reference", "identifier_required")
    now = clock.now()
    cur.execute(
        """
        INSERT INTO trs_dsr_requests (hotel_slug, type, channel, identifiers_enc, identifier_hmacs, identifiers_hint,
                                      details_enc, language, received_at, due_at, updated_at, suspected_flood)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            hotel_slug, type, channel,
            privacy.seal(identifiers.model_dump_json(exclude_none=True)),
            _hmacs(cur, hotel_slug, identifiers),
            Json(_hint(identifiers)),
            privacy.seal(details) if details else None,
            (language or "en")[:8], now, now + timedelta(days=DUE_DAYS), now, bool(suspected_flood),
        ),
    )
    row = dict(cur.fetchone())
    # The audit entry names the request, never the person.
    audit(cur, actor, "trust.dsr.create", "dsr_request", row["id"],
          after={"type": type, "channel": channel, "suspected_flood": bool(suspected_flood)}, hotel_slug=hotel_slug)
    _due_signal(cur, row)
    return row


# ─── Reads ───


def _unseal_json(blob: Any) -> dict:
    try:
        return json.loads(privacy.unseal(blob) or "{}")
    except ValueError:
        return {}


def to_api(row: dict, ctx: Any = None) -> dict:
    """A queue row for the console: sealed fields opened, then masked for
    the viewer (owner/GM see them in full; anyone else gets the masked
    hint only). Hashes and ciphertext never leave the server."""
    out = {k: v for k, v in row.items() if k not in ("identifiers_enc", "details_enc", "identifier_hmacs")}
    ids = _unseal_json(row.get("identifiers_enc")) if row.get("identifiers_enc") else {}
    details = privacy.unseal(row["details_enc"]) if row.get("details_enc") else None
    body = {"identifiers": ids, "details": details}
    fields = {"identifiers.name": pii.GUEST_NAME, "identifiers.email": pii.GUEST_CONTACT,
              "identifiers.phone": pii.GUEST_CONTACT, "details": pii.TRANSCRIPT}
    body = pii.mask_for_ctx(body, ctx, field_classes=fields) if ctx is not None else pii.mask(body, None, field_classes=fields)
    out.update(body)
    now = clock.now()
    out["overdue"] = row["status"] in OPEN_STATUSES and row["due_at"] < now
    out["days_left"] = (row["due_at"] - now).days if row["status"] in OPEN_STATUSES else None
    return out


def get_row(cur: Any, hotel_slug: str, dsr_id: int, *, lock: bool = False) -> dict:
    cur.execute(
        "SELECT * FROM trs_dsr_requests WHERE hotel_slug = %s AND id = %s" + (" FOR UPDATE" if lock else ""),
        (hotel_slug, int(dsr_id)),
    )
    r = cur.fetchone()
    if r is None:
        raise not_found("Privacy request not found", "dsr_not_found")
    return dict(r)


def list_requests(cur: Any, hotel_slug: str, *, status: str = "open", limit: int = 100) -> list[dict]:
    if status == "open":
        where, params = "status = ANY(%s)", [list(OPEN_STATUSES)]
    elif status == "all":
        where, params = "TRUE", []
    else:
        where, params = "status = %s", [status]
    cur.execute(
        f"SELECT * FROM trs_dsr_requests WHERE hotel_slug = %s AND {where} ORDER BY due_at, id LIMIT %s",
        (hotel_slug, *params, max(1, min(limit, 500))),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── State changes ───


def _update(cur: Any, dsr_id: int, **fields: Any) -> dict:
    fields["updated_at"] = clock.now()
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = [Json(v) if isinstance(v, dict) else v for v in fields.values()]
    cur.execute(f"UPDATE trs_dsr_requests SET {sets} WHERE id = %s RETURNING *", (*vals, dsr_id))
    return dict(cur.fetchone())


def verify_request(cur: Any, ctx: Any, dsr_id: int, *, method: str, note: str | None = None) -> dict:
    """Record how the requester's identity was checked. Executors refuse an
    unverified request: releasing or erasing a stranger's data on an
    unverified email is the classic DSR attack."""
    if method not in VERIFY_METHODS:
        raise ClefsError(400, f"method must be one of {', '.join(VERIFY_METHODS)}", "invalid_verification")
    row = get_row(cur, ctx.slug, dsr_id, lock=True)
    if row["status"] != "received":
        raise conflict(f"This request is {row['status']}", "dsr_not_pending")
    verification = {"method": method, "note": (note or "")[:500] or None,
                    "verified_by": ctx.principal.user_id, "verified_at": clock.now().isoformat()}
    row = _update(cur, row["id"], status="verified", verification=verification)
    audit(cur, ctx, "trust.dsr.verify", "dsr_request", row["id"], after={"method": method})
    return row


def close_request(cur: Any, ctx: Any, dsr_id: int, *, status: str, note: str | None) -> dict:
    """Reject (identity could not be verified, or an exemption applies) or
    cancel (withdrawn by the requester). The note is the reason given."""
    if status not in ("rejected", "cancelled"):
        raise ClefsError(400, "status must be rejected or cancelled", "invalid_status")
    row = get_row(cur, ctx.slug, dsr_id, lock=True)
    if row["status"] not in OPEN_STATUSES:
        raise conflict(f"This request is already {row['status']}", "dsr_closed")
    row = _update(cur, row["id"], status=status, note=(note or "")[:2000] or None, completed_at=clock.now(),
                  handled_by=ctx.principal.user_id)
    audit(cur, ctx, f"trust.dsr.{'reject' if status == 'rejected' else 'cancel'}", "dsr_request", row["id"],
          after={"status": status})
    _resolve_due_signal(cur, row)
    return row


# ─── Due-date signal and reminder ───

SIGNAL_DUE = "trust.dsr_due"
REMIND_WITHIN_DAYS = 7

signals.register_signal_kind(
    SIGNAL_DUE,
    label="Privacy request awaiting action",
    default_owner_role="owner",
    section="compliance",
    cadence="daily",
    icon="shield",
)


def _flood_key(hotel_slug: str) -> str:
    return f"{SIGNAL_DUE}:{hotel_slug}:flood"


def _flood_signal(cur: Any, hotel_slug: str) -> None:
    """One signal for every open flagged request (see create_request), kept
    in step with the queue: resolved once none is left. Its due date is the
    earliest flagged due date, so a genuine request inside a flood still
    surfaces before its deadline."""
    cur.execute(
        "SELECT count(*) AS n, min(due_at) AS due FROM trs_dsr_requests "
        "WHERE hotel_slug = %s AND suspected_flood AND status = ANY(%s)",
        (hotel_slug, list(OPEN_STATUSES)),
    )
    agg = cur.fetchone()
    if not agg["n"]:
        signals.resolve_signal(cur, hotel_slug, _flood_key(hotel_slug), resolution="flagged requests triaged")
        return
    overdue = agg["due"] < clock.now()
    signals.upsert_signal(
        cur,
        hotel_slug,
        signals.SignalIn(
            module="trust",
            kind=SIGNAL_DUE,
            dedupe_key=_flood_key(hotel_slug),
            title=f"{agg['n']} privacy requests arrived in an unusual burst",
            summary="The public privacy form took more requests than usual, so these were filed and flagged "
                    "instead of refused. Most are likely junk, but each is a legal request: check them in the "
                    "privacy queue and reject the ones that cannot be verified.",
            severity=4 if overdue else 3,
            confidence=1.0,
            owner_role="owner",
            due_at=agg["due"],
            subject_type="dsr_queue",
            subject_id="flood",
        ),
    )


def _due_signal(cur: Any, row: dict) -> None:
    if row.get("suspected_flood"):
        _flood_signal(cur, row["hotel_slug"])
        return
    now = clock.now()
    days = (row["due_at"] - now).days
    overdue = row["due_at"] < now
    title = (
        f"A privacy request ({row['type']}) is overdue" if overdue
        else f"A privacy request ({row['type']}) is due in {max(days, 0)} days"
    )
    signals.upsert_signal(
        cur,
        row["hotel_slug"],
        signals.SignalIn(
            module="trust",
            kind=SIGNAL_DUE,
            dedupe_key=f"{SIGNAL_DUE}:{row['hotel_slug']}:{row['id']}",
            title=title,
            # No personal data in a signal: it reaches the brief and WhatsApp.
            summary=f"Received {row['received_at']:%d %b %Y}, status {row['status']}. Open the privacy queue to act on it.",
            severity=4 if overdue else (3 if days <= REMIND_WITHIN_DAYS else 2),
            confidence=1.0,
            owner_role="owner",
            due_at=row["due_at"],
            subject_type="dsr_request",
            subject_id=str(row["id"]),
        ),
    )


def _resolve_due_signal(cur: Any, row: dict) -> None:
    slug = row["hotel_slug"]
    if row.get("suspected_flood"):
        _flood_signal(cur, slug)  # recounts: resolves once the last flagged one is closed
        return
    signals.resolve_signal(cur, slug, f"{SIGNAL_DUE}:{slug}:{row['id']}", resolution="request closed")


@jobs.scheduled("trust.dsr_due_reminder", jobs.daily_at("09:00"))
def _due_reminder(ctx: jobs.JobCtx) -> dict:
    rows = list_requests(ctx.cur, ctx.hotel_slug, status="open", limit=500)
    for r in rows:
        if not r.get("suspected_flood"):
            _due_signal(ctx.cur, r)
    _flood_signal(ctx.cur, ctx.hotel_slug)  # one aggregate for every flagged row
    return {"open": len(rows), "overdue": sum(1 for r in rows if r["due_at"] < ctx.now),
            "flagged": sum(1 for r in rows if r.get("suspected_flood"))}


# ─── Executors ───

RESTRICT_SCOPES = ("marketing", "ai_analytics")
OBJECT_SCOPES = ("marketing", "ai_analytics", "profile_memory")


def expand_scopes(scopes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """'marketing' is an umbrella, not one purpose: guests (04 spec 3.4)
    splits it per channel (marketing_whatsapp, marketing_email), and those
    are what journeys, can_send_marketing and marketing_audience check. So
    a restrict/object request expands it to every registered purpose named
    marketing or marketing_*, read at call time so a channel added later
    (for example marketing_sms) is covered without touching this file."""
    known = consent.purposes()
    out: list[str] = []
    for s in scopes:
        out.append(s)
        if s == "marketing":
            out += [p for p in known if p.startswith("marketing_")]
    return tuple(dict.fromkeys(out))


def _suppress_marketing(cur: Any, hotel_slug: str, ids: dict, dsr_id: int) -> int:
    """Second guard for restrict/object: a guests marketing suppression for
    each contact the requester gave. Withdrawn consent alone is undone by
    the next opt-in, and no sender reads trs_subject_flags; a suppression is
    what marketing_audience and can_send_marketing already refuse on. Goes
    through guests' public api (never its internals), and only when guests
    is loaded in this process: without it there is no guest marketing to stop."""
    from clefs.registry import enabled_modules

    if "guests" not in enabled_modules():
        return 0
    from clefs.modules.guests import api as guests_api

    n = 0
    for kind in ("phone", "email"):
        if ids.get(kind):
            guests_api.opt_out(cur, hotel_slug, kind=kind, value=ids[kind], source=f"dsr:{dsr_id}", scope="marketing")
            n += 1
    return n


def _withdraw(cur: Any, ctx: Any, sub: subject_mod.Subject, purposes: tuple[str, ...] | list[str], dsr_id: int,
              source: str) -> int:
    n = 0
    known = set(consent.purposes())
    for ref in sub.refs:
        for purpose in expand_scopes(purposes):
            if purpose in known and consent.has_consent(cur, sub.hotel_slug, ref, purpose):
                consent.withdraw(cur, sub.hotel_slug, ref, purpose, source=source, evidence={"dsr_id": dsr_id}, actor=ctx)
                n += 1
    return n


def _flag(cur: Any, sub: subject_mod.Subject, scopes: tuple[str, ...] | list[str], dsr_id: int) -> int:
    for ref in sub.refs:
        cur.execute(
            """
            INSERT INTO trs_subject_flags (hotel_slug, subject_ref, restricted, scopes, dsr_id, created_at)
            VALUES (%s, %s, TRUE, %s, %s, %s)
            ON CONFLICT (hotel_slug, subject_ref) DO UPDATE SET restricted = TRUE, lifted_at = NULL,
                scopes = ARRAY(SELECT DISTINCT unnest(trs_subject_flags.scopes || EXCLUDED.scopes)),
                dsr_id = EXCLUDED.dsr_id
            """,
            (sub.hotel_slug, ref, list(scopes), dsr_id, clock.now()),
        )
    return len(sub.refs)


def _bundle(hotel: Any, row: dict, data: dict[str, list[dict]]) -> bytes:
    """A zip with subject.json (everything, structured) and one CSV per
    section (PDPL Art. 14: "structured and machine-readable")."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        doc = {"hotel": hotel.name, "request_id": row["id"], "request_type": row["type"],
               "generated_at": clock.now().isoformat(), "sections": data}
        z.writestr("subject.json", json.dumps(doc, ensure_ascii=False, indent=2, default=str))
        for section, rows in data.items():
            if not rows:
                continue
            header = sorted({k for r in rows for k in r})
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(header)
            for r in rows:
                w.writerow([json.dumps(r.get(k), ensure_ascii=False) if isinstance(r.get(k), (dict, list))
                            else ("" if r.get(k) is None else r.get(k)) for k in header])
            z.writestr(f"{section}.csv", "﻿" + out.getvalue())
        z.writestr("README.txt", BUNDLE_README)
    return buf.getvalue()


BUNDLE_README = (
    "This archive holds the personal data the hotel keeps about you, as of the date in subject.json.\n"
    "subject.json has every section; each .csv file is one section in spreadsheet form.\n"
    "Sections: stays, contacts, consents (what you agreed to and withdrew), transcripts (your conversations\n"
    "with the hotel assistant), requests (service requests from your room during your stay), and messages or\n"
    "orders where the hotel holds them.\n\n"
    "يحتوي هذا الملف على البيانات الشخصية التي يحتفظ بها الفندق عنك. يضم ملف subject.json جميع الأقسام،\n"
    "وكل ملف ‎.csv‎ قسم واحد بصيغة جدول.\n"
)


def execute(cur: Any, ctx: Any, dsr_id: int, *, note: str | None = None, confirm: bool = False) -> dict:
    """Run the request's executor in the caller's transaction (so a failure
    leaves nothing half-erased) and close it. Needs a verified request."""
    row = get_row(cur, ctx.slug, dsr_id, lock=True)
    if row["status"] == "received":
        raise conflict("Verify the requester's identity first", "dsr_not_verified")
    if row["status"] not in OPEN_STATUSES:
        raise conflict(f"This request is already {row['status']}", "dsr_closed")
    kind = row["type"]
    if kind == "erase" and not confirm:
        raise ClefsError(400, "Erasure cannot be undone; send confirm: true", "confirm_required")
    if kind == "rectify" and not (note or "").strip():
        raise ClefsError(400, "Describe the correction made", "note_required")
    ids = _unseal_json(row["identifiers_enc"]) if row.get("identifiers_enc") else {}
    sub = subject_mod.resolve(cur, ctx.slug, ids, list(row["identifier_hmacs"] or []))
    result: dict[str, Any] = {"matched": not sub.empty, "stays": len(sub.stays), "contacts": len(sub.hmacs)}
    file_id = None
    source = f"dsr_{kind}"
    if kind in ("access", "portability"):
        data = subject_mod.collect(cur, sub)
        blob = _bundle(get_hotel(cur, ctx.slug), row, data)
        ref = files.put_file(
            cur, ctx.slug, blob, filename=f"privacy-request-{row['id']}.zip", content_type="application/zip",
            purpose="trust.dsr_bundle", source="generated", actor=ctx, retention_days=BUNDLE_KEEP_DAYS,
        )
        file_id = ref.id
        result["sections"] = {k: len(v) for k, v in data.items()}
    elif kind == "erase":
        result["erased"] = subject_mod.erase(cur, sub)
        result["consents_withdrawn"] = _withdraw(cur, ctx, sub, consent.purposes(), row["id"], source)
        result["flagged"] = _flag(cur, sub, ("all",), row["id"])
    elif kind in ("restrict", "object"):
        scopes = expand_scopes(RESTRICT_SCOPES if kind == "restrict" else OBJECT_SCOPES)
        result["consents_withdrawn"] = _withdraw(cur, ctx, sub, scopes, row["id"], source)
        result["flagged"] = _flag(cur, sub, scopes, row["id"])
        result["marketing_suppressed"] = _suppress_marketing(cur, ctx.slug, ids, row["id"])
    row = _update(
        cur, row["id"], status="completed", completed_at=clock.now(), handled_by=ctx.principal.user_id,
        result=result, result_upload_id=file_id, note=(note or "")[:2000] or None,
    )
    audit(cur, ctx, "trust.dsr.execute", "dsr_request", row["id"], after={"type": kind, "result": result})
    _resolve_due_signal(cur, row)
    return row


BUNDLE_KEEP_DAYS = 30
BUNDLE_LINK_TTL_S = 7 * 24 * 3600


def download_path(cur: Any, ctx: Any, dsr_id: int) -> dict:
    """A signed path to the access bundle (7 days), for staff to send on."""
    row = get_row(cur, ctx.slug, dsr_id)
    if not row.get("result_upload_id"):
        raise not_found("This request has no bundle", "dsr_no_bundle")
    audit(cur, ctx, "trust.dsr.download", "dsr_request", row["id"])
    return {"url": files.signed_path(ctx.slug, str(row["result_upload_id"]), ttl_s=BUNDLE_LINK_TTL_S),
            "expires_in": BUNDLE_LINK_TTL_S}
