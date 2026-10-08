"""clefs.core.pii fixes from the Wave 1 review: canonical departments,
masked national numbers, Arabic-script first names."""

from __future__ import annotations

import pytest

from clefs.core import pii

# ─── Departments (review correctness-2) ───


@pytest.mark.parametrize(
    ("viewer", "subject"),
    [
        ("Engineering", "Maintenance"),
        ("Front Office", "Front Desk"),
        ("Reception", "Front Desk"),
        ("F&B", "Food & Beverage"),
        ("Kitchen", "Food & Beverage"),
        ("housekeeping", "Housekeeping"),
        ("Spa", "SPA"),
    ],
)
def test_own_department_matches_across_vocabularies(viewer, subject):
    assert pii.policy_for(pii.TRANSCRIPT, "dept_head", departments=(viewer,), subject_department=subject) == pii.FULL


@pytest.mark.parametrize(("viewer", "subject"), [("Engineering", "Housekeeping"), ("Spa", "Front Desk")])
def test_other_departments_stay_hidden(viewer, subject):
    assert pii.policy_for(pii.TRANSCRIPT, "dept_head", departments=(viewer,), subject_department=subject) == pii.NONE


def test_unknown_or_missing_subject_department_is_nobodys():
    assert pii.policy_for(pii.STAFF_PHONE, "dept_head", departments=("Housekeeping",), subject_department=None) == pii.NONE
    assert pii.policy_for(pii.STAFF_PHONE, "dept_head", departments=("",), subject_department="") == pii.NONE


@pytest.mark.parametrize("dept", ["Food & Beverage", "F&B", "In-Room Dining", "Kitchen", "Banquets", "IRD"])
def test_fnb_check_uses_canonical_key(dept):
    assert pii.policy_for(pii.HEALTH, "staff", departments=(dept,)) == pii.FULL


def test_service_department_names_resolve():
    # Service's own names (REQUEST_TYPE_FOR_DEPARTMENT keys) all have a key.
    got = {d: pii.canonical_department(d) for d in ("Housekeeping", "Food & Beverage", "Maintenance", "Front Desk", "Concierge", "General")}
    assert got == {
        "Housekeeping": "housekeeping", "Food & Beverage": "fnb", "Maintenance": "maintenance",
        "Front Desk": "front_office", "Concierge": "concierge", "General": "general",
    }


# ─── Masked contacts (review security-privacy-5) ───


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("+971501234567", "+971••••••567"),
        ("0501234567", "•••••••567"),
        ("050 123 4567", "•••••••567"),
        ("00971501234567", "+971••••••567"),
        ("+447911123456", "+44•••••••456"),
        ("1234567", "•••••67"),
        ("1234", "••••"),
    ],
)
def test_masked_phone_reveals_at_most_three_digits(raw, masked):
    out = pii.mask({"p": raw}, "finance", field_classes={"p": pii.GUEST_CONTACT})["p"]
    assert out == masked
    shown = [ch for ch in out if ch.isdigit()]
    national = [ch for ch in raw if ch.isdigit()]
    assert len(shown) <= 3 + 3  # country code (not identifying) plus the tail
    if not raw.startswith(("+", "00")):
        assert len(shown) <= 3 and len(shown) < len(national)


def test_masked_email_keeps_one_letter():
    assert pii.mask({"e": "sam@example.com"}, "dept_head", field_classes={"e": pii.GUEST_CONTACT})["e"] == "s•••@example.com"


# ─── First names (review ux-conventions-1) ───


@pytest.mark.parametrize(
    ("name", "first"),
    [
        ("Mr John Smith", "John"),
        ("SMITH, John", "John"),
        ("المكتوم، محمد", "محمد"),  # Arabic comma: never the family name
        ("المكتوم ، محمد", "محمد"),
        ("السيد محمد بن راشد آل مكتوم", "محمد"),
        ("الشيخة فاطمة", "فاطمة"),
        ("د. أحمد الحمادي", "أحمد"),
        ("عبد الله الشامسي", "عبد الله"),
        ("Abdul Rahman Al Suwaidi", "Abdul Rahman"),
        ("Abdul bin Khalid", "Abdul"),
        ("Al Mansoori Fatima", "Fatima"),
        ("آل نهيان خالد", "خالد"),
        ("H.E. Sheikh Mohammed", "Mohammed"),
        ("He Wei", "He"),
        ("Dr.", None),
        ("Al Mansoori", None),
        ("", None),
    ],
)
def test_first_name_forms(name, first):
    assert pii.first_name(name) == first


def test_first_name_policy_never_leaks_family_name():
    out = pii.mask({"n": "AL MAKTOUM، Mohammed"}, "staff", field_classes={"n": pii.GUEST_NAME})
    assert out["n"] == "Mohammed"
