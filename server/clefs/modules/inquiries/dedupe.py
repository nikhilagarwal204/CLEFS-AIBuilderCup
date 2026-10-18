"""
Merge suggestions (04 spec 4.4): the same person without a shared contact.

Contact hmacs merge automatically at intake. What they cannot catch is a
guest who wrote once from a form without a phone and once by phone desk
without an email: a matching name plus overlapping dates within 72 h, where
one side has no contact, is offered as a suggestion in the drawer. Only a
person merges (POST /{id}/merge).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs.core.errors import ClefsError
from clefs.core.stays import name_similarity
from clefs.modules.inquiries import risk, service, store
from clefs.modules.inquiries.models import OPEN_STAGES

WINDOW = timedelta(hours=72)
MIN_NAME_SCORE = 0.9


def _span(row: dict) -> tuple[Any, Any] | None:
    if not row.get("check_in"):
        return None
    return row["check_in"], row.get("check_out") or row["check_in"] + timedelta(days=1)


def _overlap(a: dict, b: dict) -> bool:
    sa, sb = _span(a), _span(b)
    return bool(sa and sb and sa[0] < sb[1] and sb[0] < sa[1])


def suggestions(cur: Any, row: dict) -> list[dict]:
    if not row.get("contact_name") or not row.get("check_in"):
        return []
    cur.execute(
        """
        SELECT * FROM inq_inquiries
        WHERE hotel_slug = %s AND id <> %s AND duplicate_of IS NULL AND stage = ANY(%s)
          AND created_at BETWEEN %s AND %s AND contact_name IS NOT NULL AND check_in IS NOT NULL
        ORDER BY created_at DESC LIMIT 50
        """,
        (row["hotel_slug"], row["id"], list(OPEN_STAGES), row["created_at"] - WINDOW, row["created_at"] + WINDOW),
    )
    mine = set(store.hmacs_of(row))
    out = []
    for got in cur.fetchall():
        other = dict(got)
        theirs = set(store.hmacs_of(other))
        if mine and theirs:
            continue  # both have contacts: a shared one merged at intake, different ones are different people
        score = name_similarity(row["contact_name"], other["contact_name"])
        if score >= MIN_NAME_SCORE and _overlap(row, other):
            out.append({"id": other["id"], "ref": other["ref"], "source": other["source"], "score": round(score, 3),
                        "check_in": other["check_in"], "check_out": other["check_out"],
                        "created_at": other["created_at"], "stage": other["stage"]})
    return out


def merge(cur: Any, hotel: Any, row: dict, into: dict, *, actor: Any) -> dict:
    """Fold `row` into `into`: its thread moves, missing fields (contacts
    included) are filled, and `row` closes as lost SPAM_DUPLICATE with
    duplicate_of set. Returns the updated target."""
    if row["id"] == into["id"] or into.get("duplicate_of"):
        raise ClefsError(409, "cannot merge into that inquiry", "invalid_merge")
    if into["stage"] not in OPEN_STAGES:
        raise ClefsError(409, "merge into an open inquiry", "invalid_merge")
    cur.execute("UPDATE inq_messages SET inquiry_id = %s WHERE inquiry_id = %s", (into["id"], row["id"]))
    fill_cols = ("contact_name", "contact_phone_enc", "contact_phone_hmac", "contact_email_enc", "contact_email_hmac",
                 "company", "language", "check_in", "check_out", "nights", "rooms", "adults", "children",
                 "ad_referral", "ctwa_clid", "gclid", "utm", "page_url")
    changes = {k: row[k] for k in fill_cols if into.get(k) is None and row.get(k) is not None}
    changes["consent_ids"] = sorted(set(into.get("consent_ids") or []) | set(row.get("consent_ids") or []))
    if row["consent_basis"] == "express_web_form":
        changes["consent_basis"] = "express_web_form"
    target = store.update_inquiry(cur, into["id"], changes)
    target = store.update_inquiry(cur, into["id"], risk.compute(cur, hotel, target))
    store.update_inquiry(cur, row["id"], {"duplicate_of": into["id"]})
    row["duplicate_of"] = into["id"]
    service.transition(cur, row, "lost", actor=actor, reason="merged", lost_reason="SPAM_DUPLICATE",
                       data={"into": into["id"]}, hotel=hotel)
    return target
