"""
Trust's extensions to the PII masking matrix (03 spec 5.2). The five core
rows live in clefs.core.pii and cannot be replaced; these add the classes the
spec's rows do not cover, registered at import (module.py imports this file,
so the API and the worker both have them) through pii.register_field_class,
which is a no-op when the identical class is registered again.

Every cell here is asserted by tests/modules/trust/test_pii_matrix.py, next
to the core rows.
"""

from __future__ import annotations

import ipaddress
import re

from clefs.core import pii

# ─── Maskers (render the MASKED policy for one class) ───


def mask_id_document(value: str) -> str | None:
    """Passport or Emirates ID number: the last three characters only
    (enough to confirm the document at the desk, useless to anyone else)."""
    v = re.sub(r"[\s\-]", "", value or "")
    if len(v) < 6:
        return "•" * len(v) if v else None
    return "•" * (len(v) - 3) + v[-3:]


def mask_card(value: str) -> str | None:
    """A payment card: PCI DSS allows at most the last four digits."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 8:
        return "••••" if digits else None
    return "•••• " + digits[-4:]


def mask_ip(value: str) -> str | None:
    """An IP address reduced to its network: 203.0.113.7 -> 203.0.x.x,
    2001:db8::1 -> 2001:db8:x (what an audit reader needs to spot a foreign
    network, not the guest's or employee's exact address)."""
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    if ip.version == 4:
        a, b, _, _ = str(ip).split(".")
        return f"{a}.{b}.x.x"
    groups = ip.exploded.split(":")
    return f"{groups[0]}:{groups[1]}:x"


COLUMNS = ("owner", "gm", "finance", "revenue", "dept_head", "front_desk", "staff")


def _cells(owner, gm, finance, revenue, dept_head, front_desk, staff) -> dict[str, str]:
    return {
        "owner": owner, "gm": gm, "finance": finance, "revenue": revenue,
        "dept_head": dept_head, "front_desk": front_desk, "staff": staff,
    }


# ─── Classes ───

ID_DOCUMENT = "guest_id_document"  # passport / Emirates ID number
BIRTH_DATE = "guest_birth_date"
PAYMENT_CARD = "payment_card"  # a card number that reached a note or import
REQUEST_TEXT = "request_text"  # a guest's own words in a service request
AUDIT_IP = "audit_ip"  # the ip column of audit_log

F, M, N = pii.FULL, pii.MASKED, pii.NONE

TRUST_FIELD_CLASSES: dict[str, dict[str, str]] = {
    # The desk checks documents at arrival; owners and GMs recognise one by
    # its tail; nobody else ever needs it.
    ID_DOCUMENT: _cells(M, M, N, N, N, F, N),
    BIRTH_DATE: _cells(F, F, N, N, N, F, N),
    # Never shown in full to anyone: Clefs is not a card vault.
    PAYMENT_CARD: _cells(M, M, M, N, N, M, N),
    # Staff and department heads act on the request text; the transcript row
    # (core) stays stricter because a whole conversation says more.
    REQUEST_TEXT: _cells(F, F, N, N, pii.OWN_DEPARTMENT, F, F),
    # Audit readers are owner and GM (trust.audit.view); the GM sees the network only.
    AUDIT_IP: _cells(F, M, N, N, N, N, N),
}

MASKERS = {ID_DOCUMENT: mask_id_document, PAYMENT_CARD: mask_card, AUDIT_IP: mask_ip}


def register() -> None:
    """Register every Trust class (idempotent)."""
    for name, cells in TRUST_FIELD_CLASSES.items():
        pii.register_field_class(name, cells, masker=MASKERS.get(name))


register()
