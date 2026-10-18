"""
Inquiry Guard public API (frozen signatures; other modules import only this file).

Direct inquiries, quotes, payment links and OTA-leak matching. 04 spec 7.2:
every function ships as a working stub with a safe default, and the Inquiry
Guard lane replaces the bodies, never the signatures (FROZEN_API).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from clefs.core.errors import NotConfigured


class ContactIn(BaseModel):
    """A lead's contact as another module captured it (the concierge, a
    staff-logged phone call). Raw values: Inquiry Guard seals them and keeps
    only contact_hmac lookups (04 spec 4.1, F8); callers never store them."""

    name: str | None = None
    phone: str | None = None  # E.164 or national; normalised with the hotel's country
    email: str | None = None
    company: str | None = None
    language: str | None = None


def create_inquiry(
    cur: Any,
    hotel_slug: str,
    *,
    source: str,
    text: str,
    contact: ContactIn,
    check_in: date | None = None,
    check_out: date | None = None,
    rooms: int | None = None,
    consent_basis: str = "staff_logged",
    actor: Any,
) -> dict:
    """Open an inquiry from another channel (the concierge hears "can you
    quote 12 rooms for GITEX?"). Returns {id, ref, stage, outcome}. Runs the
    same intake as every channel (dedupe, reopen, SLA, extraction). Raises
    NotConfigured (409 not_configured) when Inquiry Guard is off for the
    hotel: the concierge then files a front-desk request instead."""
    from clefs.core.hotels import get_hotel, module_enabled
    from clefs.modules.inquiries.intake import Inbound, ingest
    from clefs.modules.inquiries.models import CONSENT_BASES, SOURCES

    if not module_enabled(cur, hotel_slug, "inquiries"):
        raise NotConfigured("inquiries")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if consent_basis not in CONSENT_BASES:
        raise ValueError(f"consent_basis must be one of {CONSENT_BASES}")
    hotel = get_hotel(cur, hotel_slug)
    res = ingest(cur, hotel, Inbound(
        source=source, text=text, name=contact.name, phone=contact.phone, email=contact.email,
        company=contact.company, language=contact.language, check_in=check_in, check_out=check_out,
        rooms=rooms, consent_basis=consent_basis, author_type="staff" if source == "phone_desk" else "guest",
    ), actor=actor)
    row = res.inquiry or {}
    return {"id": row.get("id"), "ref": row.get("ref"), "stage": row.get("stage"), "outcome": res.outcome}


def brief_summary(cur: Any, hotel_slug: str, *, day: date) -> dict:
    """The inquiry lines of one day's brief (Canon 18 daily: money at risk
    only). {} when Inquiry Guard is off or nothing is unanswered, so the
    brief shows no inquiry block:

    {unanswered, unanswered_over_2h, money_at_risk_minor, money_at_risk_value,
     currency, estimated: True, line, items: [{id, ref, source, segment,
     due_at, money_at_risk_minor}]}

    The line reads "2 unanswered > 2 h, AED 2.5k at risk (est.)". Awaiting
    approval comes from the quotes side once built (key absent until then)."""
    from clefs.modules.inquiries import summary

    return summary.brief_summary(cur, hotel_slug, day=day)


def verified_outcomes(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[dict]:
    """Inquiries verified as direct bookings in [start, end]:
    [{inquiry_ref, action_id, outcome_minor, currency, attribution, verified_at}].
    [] for an unknown hotel, one that switched Inquiry Guard off, or one with
    no verified direct booking in the window. outcome_minor is the net
    commission avoided; a reversed outcome (refund, PMS cancellation) drops
    out, so Trust and Finance never report a booking that fell through."""
    from clefs.core.hotels import find_hotel, module_enabled

    # WHY the guard lives here and not in verify: verify.verified_outcomes
    # resolves the hotel with get_hotel (raises on an unknown slug), and a
    # cross-module reader must get the documented [] rather than a 404.
    if find_hotel(cur, hotel_slug) is None or not module_enabled(cur, hotel_slug, "inquiries"):
        return []
    from clefs.modules.inquiries import verify

    return verify.verified_outcomes(cur, hotel_slug, start=start, end=end)


# ─── Demo seed door ───
# WHY: the UAE demo seed (clefs.seed.demo_uae) is foundation code, which may
# never import a module's internals (tests/foundation/test_module_contracts).
# It writes sent quotes directly (their send time lies in the demo's past), so
# it takes from here what quotes.deliver would have used: the Tourism Dirham
# deposit note a milestone carries and the guest-link minting. Nothing else
# calls this.


def demo_seed_door() -> Any:
    """td_deposit_note ({lang: text}) and mint_token(cur, quote) from
    inquiries/quotes.py."""
    from types import SimpleNamespace

    from clefs.modules.inquiries import quotes

    return SimpleNamespace(td_deposit_note=quotes.TD_DEPOSIT_NOTE, mint_token=quotes.mint_token)
