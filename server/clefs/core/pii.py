"""
PII masking by role (03 spec 5.2): the one matrix every guest-bearing
serializer applies before a row leaves the server.

Why it lives in the foundation and not in the trust module: service,
concierge, guests, voice, reviews and trust all serialize guest data in the
same wave, and foundation code may not import a module. So the matrix is
here, `trust.api.mask` is the module-API door to it, and Trust extends it by
registering new field classes (`register_field_class`), never by editing the
five 5.2 rows, which its test pins cell by cell.

    from clefs.core import pii
    row = pii.mask_for_ctx(row, ctx, field_classes={"name": pii.GUEST_NAME, "phone": pii.GUEST_CONTACT})
    rows = pii.mask(rows, "dept_head", departments=("Housekeeping",),
                    field_classes={"guests.name": pii.GUEST_NAME, "transcript": pii.TRANSCRIPT})

Rules that hold for every caller:
- Fail closed. An unknown or missing role (an anonymous legacy visitor, a
  magic session at another hotel) sees nothing of any class. An unknown field
  class raises ValueError, so a typo fails in tests instead of leaking.
- Hidden means None, not a missing key: client types keep their shape.
- The input is never mutated (rows may be cached); a new structure returns.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from clefs.auth.permissions import DEVICE_ROLE, ROLES
from clefs.core.privacy import mask_email

# ─── Field classes (rows) and policies (cells) ───

GUEST_NAME = "guest_name"
GUEST_CONTACT = "guest_contact"  # phone or email; the masker picks by shape
HEALTH = "health"  # preferences.health (allergies): PDPL health data
TRANSCRIPT = "transcript"
STAFF_PHONE = "staff_phone"

FULL = "full"
MASKED = "masked"
NONE = "none"
FIRST_NAME = "first_name"
OWN_DEPARTMENT = "own_department"  # full when the subject's department is one of the viewer's
FNB_ONLY = "fnb_only"  # full when the viewer works in food and beverage

# A cell may hold any policy; resolution turns the two conditional ones into
# FULL or NONE, so a leaf only ever sees one of RESOLVED_POLICIES.
POLICIES = frozenset({FULL, MASKED, NONE, FIRST_NAME, OWN_DEPARTMENT, FNB_ONLY})
RESOLVED_POLICIES = frozenset({FULL, MASKED, NONE, FIRST_NAME})

# The spec's last column is "staff (device)": a paired device sees what a
# staff member sees, since whoever holds it is on a staff PIN shift.
COLUMN_ALIASES: Mapping[str, str] = MappingProxyType({DEVICE_ROLE: "staff"})

# ─── The 03 spec 5.2 matrix ───
# Columns are the foundation roles (permissions.ROLES). Read as: "who may see
# what", least privilege for revenue (it prices rooms, it never needs a guest).

_CORE_ROWS: dict[str, dict[str, str]] = {
    GUEST_NAME: {
        "owner": FULL, "gm": FULL, "finance": FULL, "revenue": NONE,
        "dept_head": FULL, "front_desk": FULL, "staff": FIRST_NAME,
    },
    GUEST_CONTACT: {
        "owner": FULL, "gm": FULL, "finance": MASKED, "revenue": NONE,
        "dept_head": MASKED, "front_desk": FULL, "staff": NONE,
    },
    HEALTH: {
        "owner": FULL, "gm": FULL, "finance": NONE, "revenue": NONE,
        "dept_head": FNB_ONLY, "front_desk": FULL, "staff": FNB_ONLY,
    },
    TRANSCRIPT: {
        "owner": FULL, "gm": FULL, "finance": NONE, "revenue": NONE,
        "dept_head": OWN_DEPARTMENT, "front_desk": FULL, "staff": NONE,
    },
    STAFF_PHONE: {
        "owner": FULL, "gm": FULL, "finance": NONE, "revenue": NONE,
        "dept_head": OWN_DEPARTMENT, "front_desk": NONE, "staff": NONE,
    },
}
CORE_FIELD_CLASSES = frozenset(_CORE_ROWS)

_lock = threading.Lock()
_rows: dict[str, Mapping[str, str]] = {k: MappingProxyType(dict(v)) for k, v in _CORE_ROWS.items()}
_maskers: dict[str, Callable[[str], str | None]] = {}

# Live, read-only view of every class (core rows plus registered ones), for
# the Trust matrix test and console.
MATRIX: Mapping[str, Mapping[str, str]] = MappingProxyType(_rows)


def register_field_class(
    name: str, cells: Mapping[str, str], *, masker: Callable[[str], str | None] | None = None
) -> None:
    """Add a field class (for example a passport number). `cells` names a
    policy for every role in permissions.ROLES; `masker` renders MASKED for
    this class (default: the phone/email masker). The five 5.2 rows cannot
    be replaced; re-registering an identical added class is a no-op, so a
    module's startup hook may run twice."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name or ""):
        raise ValueError(f"field class {name!r} must be a lowercase identifier")
    if set(cells) != set(ROLES):
        raise ValueError(f"field class {name!r} must give a policy for exactly these roles: {ROLES}")
    bad = {r: p for r, p in cells.items() if p not in POLICIES}
    if bad:
        raise ValueError(f"field class {name!r} has unknown policies {bad}")
    row = MappingProxyType(dict(cells))
    with _lock:
        if name in CORE_FIELD_CLASSES:
            raise ValueError(f"{name!r} is a 03 spec 5.2 row and cannot be replaced")
        existing = _rows.get(name)
        if existing is not None and (dict(existing) != dict(row) or _maskers.get(name) is not masker):
            raise ValueError(f"field class {name!r} is already registered differently")
        _rows[name] = row
        if masker is not None:
            _maskers[name] = masker


# ─── Policy resolution ───

# Food and beverage under the names hotels give it. Service's department is
# "Food & Beverage" (staff_members.department); PMS and roster imports use the
# others. Compared after _norm_department.
FNB_DEPARTMENTS = frozenset(
    {"foodandbeverage", "foodbeverage", "fandb", "fnb", "fb", "kitchen", "restaurant", "inroomdining", "roomservice"}
)

# One canonical key per department, whatever vocabulary named it. WHY: the
# three sources disagree. Service files requests under "Maintenance" and
# "Front Desk" (staff_members.department, REQUEST_TYPE_FOR_DEPARTMENT), the
# team screen suggests "Engineering" and "Front Office", and devices carry
# whatever the pairing form got. Comparing raw normalised strings meant an
# Engineering head never matched a Maintenance request ("own department"
# silently hid their own team's transcripts), so both sides go through this
# table first. A name missing here keeps its normalised spelling as its key,
# so two identical unknown names still match each other.
DEPARTMENT_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        **{k: "fnb" for k in FNB_DEPARTMENTS},
        **{k: "fnb" for k in ("ird", "banquet", "banquets", "bar", "culinary", "fnbservice", "dining")},
        **{k: "housekeeping" for k in ("housekeeping", "hk", "laundry", "roomsdivisionhousekeeping")},
        **{k: "maintenance" for k in ("maintenance", "engineering", "engineer", "technical", "facilities")},
        **{k: "front_office" for k in ("frontdesk", "frontoffice", "reception", "fo", "fd", "guestrelations", "guestservices")},
        **{k: "concierge" for k in ("concierge", "belldesk", "bell", "bellboy", "luggage")},
        "security": "security",
        "spa": "spa",
        "finance": "finance",
        "general": "general",
    }
)


def _norm_department(d: Any) -> str:
    if not isinstance(d, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", d.lower().replace("&", "and"))


def canonical_department(d: Any) -> str:
    """The department's canonical key ("Food & Beverage" and "Kitchen" ->
    "fnb", "Engineering" and "Maintenance" -> "maintenance", "Front Desk" and
    "Front Office" -> "front_office"); "" for a missing or non-string name."""
    norm = _norm_department(d)
    return DEPARTMENT_ALIASES.get(norm, norm)


def column_for(role: str | None) -> str | None:
    """The matrix column for a role, or None (fail closed) when unknown. A
    non-string (a HotelCtx passed by mistake) is unknown too: use
    mask_for_ctx for a context."""
    if not isinstance(role, str) or not role:
        return None
    role = COLUMN_ALIASES.get(role, role)
    return role if role in ROLES else None


def policy_for(
    field_class: str,
    role: str | None,
    *,
    departments: Iterable[str] = (),
    subject_department: str | None = None,
) -> str:
    """The resolved policy (FULL, MASKED, NONE or FIRST_NAME) of one cell for
    one viewer. `departments` are the viewer's (HotelCtx.departments);
    `subject_department` is the record's (a request's or staff member's)."""
    row = _rows.get(field_class)
    if row is None:
        raise ValueError(f"unknown PII field class {field_class!r}")
    column = column_for(role)
    if column is None:
        return NONE
    cell = row[column]
    if cell == FNB_ONLY:
        return FULL if any(canonical_department(d) == "fnb" for d in departments) else NONE
    if cell == OWN_DEPARTMENT:
        subject = canonical_department(subject_department)
        # An unknown subject department is nobody's own department.
        return FULL if subject and subject in {canonical_department(d) for d in departments} else NONE
    return cell


# ─── Leaf renderers ───

# Leading honorifics dropped before taking a first name ("Mr John Smith"),
# Latin and Arabic script alike (UAE registration cards and PMS profiles hold
# both). Compared after lowercasing and stripping trailing dots, so "Dr." and
# "H.E." match; a bare "He" (a Chinese given name) is not the title "H.E.".
_TITLES = frozenset(
    {
        "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam", "sheikh", "sheikha", "sh", "shk",
        "h.e", "h.h", "hh", "his", "her", "highness", "excellency", "eng", "engr", "capt", "sayed", "sayyid",
        "السيد", "السيدة", "الآنسة", "الانسة", "السيده", "سيد", "سيدة", "الشيخ", "الشيخة", "شيخ", "شيخة",
        "د", "الدكتور", "الدكتورة", "دكتور", "دكتورة", "المهندس", "المهندسة", "م", "سمو", "صاحب", "معالي",
        "سعادة", "الأستاذ", "الاستاذ", "الأستاذة", "الاستاذة", "أ",
    }
)
# A family-name particle leading the name ("Al Mansoori Fatima", "آل مكتوم
# محمد") means the family name came first; it and the family name are
# skipped so they never reach a device.
_FAMILY_PARTICLES = frozenset({"al", "el", "aal", "al-", "el-", "آل", "ال"})
# Given names that are two words in Arabic naming ("Abdul Rahman",
# "عبد الله", "Abu Bakr"): the first word alone is not the person's name.
_COMPOUND_HEADS = frozenset({"abd", "abdul", "abdel", "abdal", "abdoul", "abu", "abou", "عبد", "أبو", "ابو"})
# Latin and Arabic commas (U+060C) both separate "FAMILY, Given".
_NAME_COMMA = re.compile(r"[,،]")


def _title_key(token: str) -> str:
    return token.lower().rstrip(".").strip("‏‎")


def first_name(name: Any) -> str | None:
    """The given name shown to staff devices: 'Mr John Smith' -> 'John', the
    PMS order 'SMITH, John' (or 'المكتوم، محمد' with the Arabic comma) ->
    the given name, 'Abdul Rahman Al Suwaidi' -> 'Abdul Rahman'. Never the
    family name: None when nothing else is left."""
    if not isinstance(name, str):
        return None
    parts = _NAME_COMMA.split(name, maxsplit=1)
    if len(parts) == 2:
        name = parts[1]
    tokens = [t.strip(".,،;:()\"'") for t in name.split()]
    tokens = [t for t in tokens if t]
    i = 0
    while i < len(tokens) and _title_key(tokens[i]) in _TITLES:
        i += 1
    if i < len(tokens) and _title_key(tokens[i]) in _FAMILY_PARTICLES:
        i += 2  # the particle and the family name it introduces
        while i < len(tokens) and _title_key(tokens[i]) in _TITLES:
            i += 1
    if i >= len(tokens):
        return None
    given = tokens[i]
    if _title_key(given) in _COMPOUND_HEADS and i + 1 < len(tokens):
        nxt = tokens[i + 1]
        if _title_key(nxt) not in _FAMILY_PARTICLES and _title_key(nxt) not in {"bin", "bint", "ibn", "بن", "بنت", "ابن"}:
            return f"{given} {nxt}"
    return given


# ─── Contact masking ───
# WHY not privacy.mask_phone alone: it keeps the first four characters and
# the last four, which for a national number ("0501234567") shows eight of
# ten digits, enough to call the guest. Here the masked policy reveals the
# country code (not identifying) and at most the last three digits.


def _calling_code(digits: str) -> str:
    from clefs.core.privacy import CALLING_CODES

    codes = set(CALLING_CODES.values())
    for n in (3, 2, 1):
        if digits[:n] in codes:
            return digits[:n]
    return ""


def mask_contact_phone(value: str) -> str:
    """'+971501234567' -> '+971••••••567', '0501234567' -> '•••••••567',
    a short number -> its last two digits at most."""
    raw = value.strip()
    digits = re.sub(r"\D", "", raw)
    international = raw.startswith("+") or raw.startswith("00")
    if international and raw.startswith("00"):
        digits = digits[2:]
    prefix = ""
    if international:
        cc = _calling_code(digits)
        prefix, digits = "+" + cc, digits[len(cc):]
    if len(digits) >= 9:
        keep = 3
    elif len(digits) >= 6:
        keep = 2
    else:
        keep = 0
    return prefix + "•" * (len(digits) - keep) + (digits[-keep:] if keep else "") if digits else "•••"


def _mask_contact(value: str) -> str | None:
    return mask_email(value) if "@" in value else mask_contact_phone(value)


def _render(value: Any, policy: str, field_class: str) -> Any:
    if value is None or policy == FULL:
        return value
    if policy == NONE:
        return None
    if isinstance(value, (list, tuple)):
        # A list of names or numbers: render each, keep the list.
        return type(value)(_render(v, policy, field_class) for v in value)
    if not isinstance(value, str):
        # A number, dict or anything else cannot be partially shown: hide it.
        return None
    if policy == FIRST_NAME:
        return first_name(value)
    return _maskers.get(field_class, _mask_contact)(value)


# ─── Masking ───


def _copy(obj: Any) -> Any:
    # Containers are rebuilt (psycopg2 RealDictRow becomes a plain dict);
    # leaves are shared, they are replaced, never mutated.
    if isinstance(obj, BaseModel):
        return _copy(obj.model_dump())
    if isinstance(obj, Mapping):
        return {k: _copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_copy(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_copy(v) for v in obj)
    return obj


def _apply(
    node: Any,
    parts: list[str],
    field_class: str,
    role: str | None,
    departments: tuple[str, ...],
    subject_department: str | None,
    department_field: str | None,
    explicit_department: bool,
) -> Any:
    if isinstance(node, (list, tuple)):
        return type(node)(
            _apply(x, parts, field_class, role, departments, subject_department, department_field, explicit_department)
            for x in node
        )
    if not isinstance(node, dict):
        return node
    # The nearest enclosing record's own department decides "own department"
    # (a list of requests, each with its department), unless the caller
    # named the subject's department explicitly.
    if not explicit_department and department_field and department_field in node:
        subject_department = node.get(department_field)
    key = parts[0]
    if key not in node:
        return node
    if len(parts) == 1:
        policy = policy_for(field_class, role, departments=departments, subject_department=subject_department)
        node[key] = _render(node[key], policy, field_class)
    else:
        node[key] = _apply(
            node[key], parts[1:], field_class, role, departments, subject_department, department_field, explicit_department
        )
    return node


def mask(
    obj: Any,
    role: str | None,
    *,
    field_classes: Mapping[str, str],
    departments: Iterable[str] = (),
    subject_department: str | None = None,
    department_field: str | None = "department",
) -> Any:
    """A masked copy of `obj` (a dict, a pydantic model, or a list/tuple of
    them; a model comes back as a dict) for a viewer with `role`.

    field_classes maps dotted paths to field classes: {"name": GUEST_NAME,
    "guests.name": GUEST_NAME, "preferences.health": HEALTH}. A list met on
    the way (top level or nested) applies the rest of the path to each item;
    a missing key is skipped. `departments` are the viewer's. The subject's
    department is `subject_department` when given, else the value of
    `department_field` on the nearest enclosing dict (None disables that)."""
    for path, field_class in field_classes.items():
        if field_class not in _rows:
            raise ValueError(f"unknown PII field class {field_class!r} for {path!r}")
        if not path or any(not p for p in path.split(".")):
            raise ValueError(f"bad field path {path!r}")
    out = _copy(obj)
    viewer_departments = tuple(d for d in departments if isinstance(d, str))
    explicit = subject_department is not None
    for path, field_class in field_classes.items():
        out = _apply(
            out, path.split("."), field_class, role, viewer_departments, subject_department, department_field, explicit
        )
    return out


def role_for_ctx(ctx: Any) -> str | None:
    """The matrix role of a HotelCtx: its membership role ("device" for a
    paired device), "owner" for a Clefs admin acting as one (admins pass
    every check, and deps audits the bypass), else None: an anonymous legacy
    visitor sees nothing, so a legacy route that must keep its golden shape
    does not run its rows through mask for such a visitor."""
    if getattr(ctx, "role", None):
        return ctx.role
    from clefs.auth.deps import has_admin_powers  # lazy: deps pulls in FastAPI and the pool

    principal = getattr(ctx, "principal", None)
    if principal is not None and has_admin_powers(principal):
        return "owner"
    return None


def mask_for_ctx(
    obj: Any,
    ctx: Any,
    *,
    field_classes: Mapping[str, str],
    subject_department: str | None = None,
    department_field: str | None = "department",
) -> Any:
    """mask() for the viewer behind a HotelCtx (role and departments)."""
    return mask(
        obj,
        role_for_ctx(ctx),
        field_classes=field_classes,
        departments=getattr(ctx, "departments", ()) or (),
        subject_department=subject_department,
        department_field=department_field,
    )
