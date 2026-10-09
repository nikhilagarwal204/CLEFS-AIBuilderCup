"""
Read-side Service configuration: the hotel's overrides (svc_departments,
svc_request_types, svc_sla_policies) merged over the global defaults in
constants.py (03 spec 1.3 step 7).

Why merge in Python instead of seeding NULL-hotel rows: a default revised
after a review reaches every hotel that never overrode it, and the rules stay
testable without a database. The console's PUT config endpoints write only
the rows a hotel changed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clefs.core import time as htime
from clefs.modules.service import constants as C

# ─── Departments ───


def departments(cur: Any, hotel_slug: str) -> dict[str, dict]:
    """key -> {key, name, name_ar, legacy_category, color, active, sort}."""
    out = {k: {"key": k, "active": True, **v} for k, v in C.DEPARTMENTS.items()}
    cur.execute(
        "SELECT key, name, name_ar, legacy_category, color, active, sort FROM svc_departments WHERE hotel_slug = %s",
        (hotel_slug,),
    )
    for r in cur.fetchall():
        out[r["key"]] = {**out.get(r["key"], {}), **dict(r)}
    return out


def department(cur: Any, hotel_slug: str, key: str | None) -> dict:
    deps = departments(cur, hotel_slug)
    return deps.get(key or "") or deps["general"]


def department_for(cur: Any, hotel_slug: str, value: str | None) -> str | None:
    """A free-text department (legacy category, device scope, model output,
    staff_members.department) as a key, hotel-defined names included."""
    key = C.department_key(value)
    if key or not value:
        return key
    v = str(value).strip().lower()
    for k, d in departments(cur, hotel_slug).items():
        if v in (str(d.get("name") or "").lower(), str(d.get("legacy_category") or "").lower()):
            return k
    return None


def staff_department_names(cur: Any, hotel_slug: str, key: str) -> list[str]:
    """Every staff_members.department spelling that means `key` (the legacy
    column is free text: "Food & Beverage", "F&B", "In-Room Dining")."""
    d = department(cur, hotel_slug, key)
    names = {d.get("legacy_category"), d.get("name"), key}
    names |= {alias for alias, k in C.DEPARTMENT_ALIASES.items() if k == key}
    return sorted({str(n).lower() for n in names if n})


# ─── Request types ───


def request_types(cur: Any, hotel_slug: str) -> dict[str, dict]:
    """key -> type. `needs_proof` (05 decision 8) is the default from
    constants.PROOF_TYPES unless the hotel's row sets it."""
    out = {k: {"key": k, "sensitive": False, "active": True, "needs_proof": k in C.PROOF_TYPES, **v}
           for k, v in C.REQUEST_TYPES.items()}
    cur.execute(
        """SELECT key, label_en, label_ar, department_key, default_severity, is_amenity, sensitive, legacy_type, active,
                  needs_proof
           FROM svc_request_types WHERE hotel_slug = %s""",
        (hotel_slug,),
    )
    for r in cur.fetchall():
        base = out.get(r["key"]) or {"needs_proof": r["key"] in C.PROOF_TYPES}
        out[r["key"]] = {**base, **{k: v for k, v in dict(r).items() if v is not None}}
    return out


def needs_proof(cur: Any, hotel_slug: str, request_type_key: str | None) -> bool:
    """Does a job of this type close only with a checked photo?"""
    return bool(request_types(cur, hotel_slug).get(request_type_key or "", {}).get("needs_proof"))


def _refine(key: str | None, text: str, types: dict[str, dict]) -> str | None:
    """A keyword match inside the text (an AC complaint filed as
    "Maintenance" is an AC request: its own SLA and the heat rule). A match is
    taken only when it stays in the base type's department, or when the base
    type is unknown or generic, so "bring towels with dinner" stays dining."""
    padded = f" {text.lower()} "
    base_dept = types.get(key or "", {}).get("department_key")
    for cand, words in C.TYPE_KEYWORDS:
        if cand == key or cand not in types or not any(w in padded for w in words):
            continue
        if key in (None, "other") or types[cand].get("department_key") == base_dept:
            return cand
    return None


def resolve_request_type(
    cur: Any, hotel_slug: str, *, type: str | None, description: str | None = None, key: str | None = None
) -> tuple[str, bool]:
    """(request_type_key, known). `key` is a v2 key from the tool executors
    or the console; `type` a legacy type string ("Towel", "In-Room Dining").
    known=False means neither named a real type: the caller then routes by
    the model's category, the way the legacy auto-assigner did."""
    types = request_types(cur, hotel_slug)
    base: str | None = None
    if key and key in types:
        base = key
    elif type:
        t = str(type).strip()
        if t.lower().replace(" ", "_") in types:
            base = t.lower().replace(" ", "_")
        else:
            base = C.LEGACY_TYPE_TO_KEY.get(t.lower())
            if base is None:
                for k, v in types.items():
                    if str(v.get("legacy_type") or "").lower() == t.lower() or str(v.get("label_en") or "").lower() == t.lower():
                        base = k
                        break
    refined = _refine(base, f"{type or ''} {description or ''}", types)
    final = refined or base
    return (final or "other"), final is not None


def request_type(cur: Any, hotel_slug: str, key: str) -> dict:
    types = request_types(cur, hotel_slug)
    return types.get(key) or types["other"]


# ─── SLA policies ───
# The most specific policy wins: request_type, then department+severity, then
# department, then severity, then the hotel default, then the global default;
# a hotel row before the global seed at the same scope.


def _hotel_policies(cur: Any, hotel_slug: str) -> dict[tuple[str, str], dict]:
    cur.execute(
        """SELECT id, scope_type, scope_key, ack_min, resolve_min, due_local, ladder, conditions
           FROM svc_sla_policies WHERE hotel_slug = %s AND active""",
        (hotel_slug,),
    )
    return {(r["scope_type"], r["scope_key"] or ""): dict(r) for r in cur.fetchall()}


def _policy(ref: str, ack: int, resolve: int, *, due_local=None, ladder=None) -> dict:
    return {"ref": ref, "ack_min": ack, "resolve_min": resolve, "due_local": due_local, "ladder": list(ladder or C.DEFAULT_LADDER)}


def sla_policy(cur: Any, hotel_slug: str, *, request_type_key: str | None, department_key: str | None, severity: str | None) -> dict:
    """{ref, ack_min, resolve_min, due_local, ladder}. `ref` names the match
    ("hotel:request_type:towels", "global:department:ird") for the console."""
    rows = _hotel_policies(cur, hotel_slug)
    chain: list[tuple[str, str]] = [
        ("request_type", request_type_key or ""),
        ("department_severity", f"{department_key}:{severity}"),
        ("department", department_key or ""),
        ("severity", severity or ""),
        ("default", ""),
    ]
    policy: dict | None = None
    for scope, skey in chain:
        r = rows.get((scope, skey))
        if r is not None:
            policy = _policy(f"hotel:{scope}:{skey}", r["ack_min"], r["resolve_min"], due_local=r["due_local"], ladder=r["ladder"])
            break
        g = _global(scope, skey)
        if g is not None:
            policy = g
            break
    policy = policy or _policy("global:default", *C.SLA_DEFAULT)
    # L1 caps the ack time (respond now, the GM hears at creation); the fix
    # time stays the type's own (an AC is still "attend 5, fix or move 60").
    if severity == "L1" and "L1" in C.SLA_SEVERITY:
        policy["ack_min"] = min(policy["ack_min"], C.SLA_SEVERITY["L1"][0])
    return policy


def _global(scope: str, skey: str) -> dict | None:
    if scope == "request_type" and skey in C.SLA_REQUEST_TYPE:
        return _policy(f"global:request_type:{skey}", *C.SLA_REQUEST_TYPE[skey], due_local=C.SLA_DUE_LOCAL.get(skey))
    if scope == "department" and skey in C.SLA_DEPARTMENT:
        return _policy(f"global:department:{skey}", *C.SLA_DEPARTMENT[skey])
    if scope == "severity" and skey in C.SLA_SEVERITY:
        return _policy(f"global:severity:{skey}", *C.SLA_SEVERITY[skey])
    return None


def due_times(hotel: Any, policy: dict, *, start_ms: int) -> tuple[int, int]:
    """(ack_due_at, resolve_due_at) in epoch ms from `start_ms` (creation,
    release or reopen). A window policy (turndown) is due at its local time
    when requested before it, else it falls back to the plain minutes."""
    ack = start_ms + int(policy["ack_min"]) * 60_000
    resolve = start_ms + int(policy["resolve_min"]) * 60_000
    due_local = policy.get("due_local")
    if due_local is not None:
        local = htime.from_ms(start_ms).astimezone(htime.tz_of(hotel))
        window = local.replace(hour=due_local.hour, minute=due_local.minute, second=0, microsecond=0)
        if local < window:
            resolve = htime.to_ms(window)
    return ack, max(resolve, ack)


def shift_key(cur: Any, hotel: Any, at: datetime) -> str | None:
    """The general (department-less) shift template covering `at` in hotel
    time: the hotel's own templates first, the seeds otherwise."""
    cur.execute(
        "SELECT key, start_time, end_time FROM svc_shift_templates WHERE hotel_slug = %s AND active AND department_key IS NULL ORDER BY id",
        (hotel.slug,),
    )
    rows = [dict(r) for r in cur.fetchall()] or [t for t in C.SHIFT_TEMPLATES if t["department_key"] is None]
    t = at.astimezone(htime.tz_of(hotel)).time()
    for r in rows:
        s, e = r["start_time"], r["end_time"]
        if (s <= t < e) if s < e else (t >= s or t < e):
            return r["key"]
    return None

