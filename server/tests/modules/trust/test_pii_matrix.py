"""The PII masking matrix, every cell (03 spec 5.2), plus Trust's registered
classes and the renderers behind the masked and first-name policies."""

from __future__ import annotations

import pytest

from clefs.auth.permissions import ROLES
from clefs.core import pii
from clefs.modules import trust  # noqa: F401
from clefs.modules.trust import api as trust_api
from clefs.modules.trust import pii_classes as pc

F, M, N, FN, OWN, FNB = pii.FULL, pii.MASKED, pii.NONE, pii.FIRST_NAME, pii.OWN_DEPARTMENT, pii.FNB_ONLY
COLS = ("owner", "gm", "finance", "revenue", "dept_head", "front_desk", "staff")

# The spec table, row by row, in COLS order. Changing a cell here is a
# privacy decision: it needs the spec to change first.
EXPECTED = {
    pii.GUEST_NAME: (F, F, F, N, F, F, FN),
    pii.GUEST_CONTACT: (F, F, M, N, M, F, N),
    pii.HEALTH: (F, F, N, N, FNB, F, FNB),
    pii.TRANSCRIPT: (F, F, N, N, OWN, F, N),
    pii.STAFF_PHONE: (F, F, N, N, OWN, N, N),
    pc.ID_DOCUMENT: (M, M, N, N, N, F, N),
    pc.BIRTH_DATE: (F, F, N, N, N, F, N),
    pc.PAYMENT_CARD: (M, M, M, N, N, M, N),
    pc.REQUEST_TEXT: (F, F, N, N, OWN, F, F),
    pc.AUDIT_IP: (F, M, N, N, N, N, N),
}

SAMPLES = {
    pii.GUEST_NAME: "Mr John Smith",
    pii.GUEST_CONTACT: "+971501234567",
    pii.HEALTH: "peanut allergy",
    pii.TRANSCRIPT: "I need towels",
    pii.STAFF_PHONE: "+971509876543",
    pc.ID_DOCUMENT: "P12345678",
    pc.BIRTH_DATE: "1980-01-02",
    pc.PAYMENT_CARD: "4111 1111 1111 1234",
    pc.REQUEST_TEXT: "Extra pillow please",
    pc.AUDIT_IP: "203.0.113.7",
}
RENDERED_MASKED = {
    pii.GUEST_CONTACT: "+971••••••567",
    pc.ID_DOCUMENT: "••••••678",
    pc.PAYMENT_CARD: "•••• 1234",
    pc.AUDIT_IP: "203.0.x.x",
}


def test_matrix_columns_are_the_foundation_roles():
    assert tuple(ROLES) == COLS


def test_every_registered_class_is_pinned():
    """Core and Trust rows are pinned below. A class another module registers
    is its owner's decision; here it must still be a complete, valid row."""
    assert set(EXPECTED) <= set(pii.MATRIX)
    for name in set(pii.MATRIX) - set(EXPECTED):
        assert set(pii.MATRIX[name]) == set(COLS) and set(pii.MATRIX[name].values()) <= pii.POLICIES, name


@pytest.mark.parametrize("field_class", sorted(EXPECTED))
def test_every_cell_of_the_matrix(field_class):
    assert tuple(pii.MATRIX[field_class][c] for c in COLS) == EXPECTED[field_class]


def _expected_value(field_class: str, cell: str, *, in_scope: bool):
    v = SAMPLES[field_class]
    if cell == F or (cell in (OWN, FNB) and in_scope):
        return v
    if cell == M:
        return RENDERED_MASKED[field_class]
    if cell == FN:
        return "John"
    return None


@pytest.mark.parametrize("field_class", sorted(EXPECTED))
@pytest.mark.parametrize("role", COLS)
def test_every_cell_renders_through_mask(field_class, role):
    """Each cell through the real serializer path, in and out of scope for
    the two conditional policies (F&B viewer / same department)."""
    cell = EXPECTED[field_class][COLS.index(role)]
    row = {"department": "Food & Beverage", "value": SAMPLES[field_class]}
    fields = {"value": field_class}
    inside = trust_api.mask(row, role, field_classes=fields, departments=("Kitchen",))
    outside = trust_api.mask(row, role, field_classes=fields, departments=("Housekeeping",))
    assert inside["value"] == _expected_value(field_class, cell, in_scope=True)
    assert outside["value"] == _expected_value(field_class, cell, in_scope=False)
    assert row["value"] == SAMPLES[field_class]  # never mutated


def test_device_column_is_staff_and_unknown_roles_see_nothing():
    row = {"name": "Mr John Smith", "phone": "+971501234567"}
    fields = {"name": pii.GUEST_NAME, "phone": pii.GUEST_CONTACT}
    assert trust_api.mask(row, "device", field_classes=fields) == {"name": "John", "phone": None}
    for role in (None, "", "supervisor", "clefs_admin"):
        assert trust_api.mask(row, role, field_classes=fields) == {"name": None, "phone": None}


def test_trust_classes_register_idempotently():
    pc.register()
    pc.register()
    with pytest.raises(ValueError):
        pii.register_field_class(pc.AUDIT_IP, dict.fromkeys(COLS, F))
