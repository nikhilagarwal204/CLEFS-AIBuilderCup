"""
Service configuration routes (03 spec 1.7): departments, request types,
amenities (through amenities.api), SLA policies and the compensation
authority, under /hotels/{slug}/service/config/...

Global defaults live in constants.py; the tables hold only what a hotel
changed (0101). A GET returns the merged view, each row marked `source:
default|hotel`; a PUT upserts the hotel's rows, and `reset: [keys]` drops
overrides so the default shows through again.

This file also gathers the ops part's routers (OPS_ROUTERS) so the manifest
mounts them with one import, and imports whatsapp.py so its inbound handler
registers with clefs.notify.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm, require_perm_or_device
from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.amenities import api as amenities_api
from clefs.modules.service import config
from clefs.modules.service import constants as C

router = APIRouter(prefix="/hotels/{slug}/service/config", tags=["service-config"])

KEY_PATTERN = r"^[a-z0-9_]{1,40}$"


def _sources(cur: Any, table: str, hotel_slug: str, key_col: str = "key") -> set[str]:
    cur.execute(f"SELECT {key_col} FROM {table} WHERE hotel_slug = %s", (hotel_slug,))  # noqa: S608 - fixed names
    return {r[key_col] for r in cur.fetchall()}


def _reset(cur: Any, table: str, hotel_slug: str, keys: list[str], key_col: str = "key") -> int:
    if not keys:
        return 0
    cur.execute(f"DELETE FROM {table} WHERE hotel_slug = %s AND {key_col} = ANY(%s)", (hotel_slug, keys))  # noqa: S608
    return cur.rowcount


# ─── Departments ───


def list_departments(cur: Any, hotel_slug: str) -> list[dict]:
    hotel_rows = _sources(cur, "svc_departments", hotel_slug)
    out = []
    for k, d in config.departments(cur, hotel_slug).items():
        out.append({"key": k, "name": d.get("name"), "name_ar": d.get("name_ar"),
                    "legacy_category": d.get("legacy_category"), "color": d.get("color"), "active": d.get("active", True),
                    "sort": d.get("sort", 0), "source": "hotel" if k in hotel_rows else "default"})
    return sorted(out, key=lambda d: (d["sort"] or 0, d["key"]))


class DepartmentIn(BaseModel):
    key: str = Field(..., pattern=KEY_PATTERN)
    name: str = Field(..., min_length=1, max_length=80)
    name_ar: str | None = Field(None, max_length=80)
    legacy_category: str | None = Field(None, max_length=80)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    active: bool = True
    sort: int = Field(0, ge=0, le=1000)


class DepartmentsIn(BaseModel):
    departments: list[DepartmentIn] = Field(default_factory=list, max_length=100)
    reset: list[str] = Field(default_factory=list, max_length=100)


@router.get("/departments")
def get_departments(slug: str, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return list_departments(cur, slug)


@router.put("/departments")
def put_departments(slug: str, body: DepartmentsIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> list[dict]:
    """Upsert the hotel's departments. legacy_category (what old clients
    read in staff_requests.category) defaults to the global one, else the
    name; `reset` drops overrides."""
    with db.db() as cur:
        before = list_departments(cur, slug)
        _reset(cur, "svc_departments", slug, body.reset)
        for d in body.departments:
            legacy = d.legacy_category or C.DEPARTMENTS.get(d.key, {}).get("legacy_category") or d.name
            cur.execute(
                """INSERT INTO svc_departments (hotel_slug, key, name, name_ar, legacy_category, color, active, sort)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (hotel_slug, key) DO UPDATE SET name = EXCLUDED.name, name_ar = EXCLUDED.name_ar,
                       legacy_category = EXCLUDED.legacy_category, color = EXCLUDED.color, active = EXCLUDED.active,
                       sort = EXCLUDED.sort, updated_at = NOW()""",
                (slug, d.key, d.name, d.name_ar, legacy, d.color, d.active, d.sort),
            )
        after = list_departments(cur, slug)
        audit(cur, ctx, "service.config.departments", "svc_departments", slug, before={"rows": before},
              after={"rows": after}, hotel_slug=slug)
        return after


# ─── Request types ───


def list_request_types(cur: Any, hotel_slug: str) -> list[dict]:
    hotel_rows = _sources(cur, "svc_request_types", hotel_slug)
    keys = ("label_en", "label_ar", "department_key", "default_severity", "is_amenity", "sensitive", "legacy_type", "active")
    out = [{"key": k, **{f: t.get(f) for f in keys}, "source": "hotel" if k in hotel_rows else "default"}
           for k, t in config.request_types(cur, hotel_slug).items()]
    return sorted(out, key=lambda t: (t["department_key"] or "", t["key"]))


class RequestTypeIn(BaseModel):
    key: str = Field(..., pattern=KEY_PATTERN)
    label_en: str = Field(..., min_length=1, max_length=80)
    label_ar: str | None = Field(None, max_length=80)
    department_key: str = Field(..., pattern=KEY_PATTERN)
    default_severity: Literal["L1", "L2", "L3"] = "L3"
    is_amenity: bool = False
    sensitive: bool = False
    legacy_type: str | None = Field(None, max_length=80)
    active: bool = True


class RequestTypesIn(BaseModel):
    request_types: list[RequestTypeIn] = Field(default_factory=list, max_length=200)
    reset: list[str] = Field(default_factory=list, max_length=200)


@router.get("/request-types")
def get_request_types(slug: str, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return list_request_types(cur, slug)


@router.put("/request-types")
def put_request_types(slug: str, body: RequestTypesIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> list[dict]:
    with db.db() as cur:
        depts = config.departments(cur, slug)
        unknown = sorted({t.department_key for t in body.request_types} - set(depts))
        if unknown:
            raise ClefsError(400, f"Unknown department {unknown[0]}", "unknown_department")
        before = list_request_types(cur, slug)
        _reset(cur, "svc_request_types", slug, body.reset)
        for t in body.request_types:
            cur.execute(
                """INSERT INTO svc_request_types (hotel_slug, key, label_en, label_ar, department_key, default_severity,
                       is_amenity, sensitive, legacy_type, active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (hotel_slug, key) DO UPDATE SET label_en = EXCLUDED.label_en, label_ar = EXCLUDED.label_ar,
                       department_key = EXCLUDED.department_key, default_severity = EXCLUDED.default_severity,
                       is_amenity = EXCLUDED.is_amenity, sensitive = EXCLUDED.sensitive,
                       legacy_type = EXCLUDED.legacy_type, active = EXCLUDED.active, updated_at = NOW()""",
                (slug, t.key, t.label_en, t.label_ar, t.department_key, t.default_severity, t.is_amenity, t.sensitive,
                 t.legacy_type, t.active),
            )
        after = list_request_types(cur, slug)
        audit(cur, ctx, "service.config.request_types", "svc_request_types", slug,
              before={"n_hotel": sum(1 for r in before if r["source"] == "hotel")},
              after={"n_hotel": sum(1 for r in after if r["source"] == "hotel"),
                     "keys": [t.key for t in body.request_types], "reset": body.reset}, hotel_slug=slug)
        return after

# ─── Amenities ───
# The catalog belongs to the Amenities module (Canon 8, amn_items); Service
# reads it through amenities.api and shows which request type each item
# files. Editing happens in the Amenities console.


@router.get("/amenities")
def get_amenities(slug: str, language: str = "en",
                  ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        types = config.request_types(cur, slug)
        out = []
        for item in amenities_api.list_items(cur, slug, language=language, active_only=False) or []:
            rt = types.get(item.get("request_type") or "", {})
            out.append({**item, "request_type_label": rt.get("label_en"), "department_key": rt.get("department_key")})
        return out


@router.put("/amenities")
def put_amenities(slug: str, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    raise ClefsError(409, "Amenity items are edited in the Amenities module", "managed_by_amenities")


# ─── SLA policies ───

SCOPES = ("request_type", "department_severity", "department", "severity", "default")


def list_sla_policies(cur: Any, hotel_slug: str) -> list[dict]:
    """The hotel's rows plus the global seeds they have not overridden."""
    cur.execute("SELECT * FROM svc_sla_policies WHERE hotel_slug = %s ORDER BY scope_type, scope_key", (hotel_slug,))
    rows = []
    seen = set()
    for r in cur.fetchall():
        seen.add((r["scope_type"], r["scope_key"]))
        rows.append({"id": r["id"], "scope_type": r["scope_type"], "scope_key": r["scope_key"], "ack_min": r["ack_min"],
                     "resolve_min": r["resolve_min"], "due_local": r["due_local"].strftime("%H:%M") if r["due_local"] else None,
                     "ladder": r["ladder"] or list(C.DEFAULT_LADDER), "conditions": r["conditions"] or {},
                     "active": r["active"], "source": "hotel"})
    seeds = [("request_type", k, v) for k, v in C.SLA_REQUEST_TYPE.items()]
    seeds += [("department", k, v) for k, v in C.SLA_DEPARTMENT.items()]
    seeds += [("severity", k, v) for k, v in C.SLA_SEVERITY.items()] + [("default", "", C.SLA_DEFAULT)]
    for scope, key, (ack, res) in seeds:
        if (scope, key) in seen:
            continue
        due = C.SLA_DUE_LOCAL.get(key) if scope == "request_type" else None
        rows.append({"id": None, "scope_type": scope, "scope_key": key, "ack_min": ack, "resolve_min": res,
                     "due_local": due.strftime("%H:%M") if due else None, "ladder": list(C.DEFAULT_LADDER),
                     "conditions": {}, "active": True, "source": "default"})
    return sorted(rows, key=lambda r: (SCOPES.index(r["scope_type"]), r["scope_key"]))


class LadderStep(BaseModel):
    after_min: int = Field(..., ge=0, le=1440)
    to: Literal["assignee", "supervisor", "duty_manager", "gm"]


class SlaPolicyIn(BaseModel):
    scope_type: Literal["request_type", "department_severity", "department", "severity", "default"]
    scope_key: str = Field("", max_length=80)
    ack_min: int = Field(..., ge=0, le=1440)
    resolve_min: int = Field(..., ge=1, le=10080)
    due_local: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    ladder: list[LadderStep] | None = Field(None, max_length=8)
    conditions: dict = Field(default_factory=dict)
    active: bool = True


class SlaKey(BaseModel):
    scope_type: Literal["request_type", "department_severity", "department", "severity", "default"]
    scope_key: str = ""


class SlaPoliciesIn(BaseModel):
    policies: list[SlaPolicyIn] = Field(default_factory=list, max_length=300)
    reset: list[SlaKey] = Field(default_factory=list, max_length=300)


def _check_scope(cur: Any, slug: str, p: SlaPolicyIn) -> None:
    types, depts = config.request_types(cur, slug), config.departments(cur, slug)
    k = p.scope_key
    ok = {
        "request_type": k in types,
        "department": k in depts,
        "severity": k in C.SEVERITIES,
        "default": k == "",
        "department_severity": ":" in k and k.split(":", 1)[0] in depts and k.split(":", 1)[1] in C.SEVERITIES,
    }[p.scope_type]
    if not ok:
        raise ClefsError(400, f"Unknown {p.scope_type.replace('_', ' ')} '{k}'", "unknown_scope")
    if p.ack_min > p.resolve_min:
        raise ClefsError(400, "The acknowledge time cannot be longer than the resolve time", "bad_request")
    if p.ladder:
        mins = [s.after_min for s in p.ladder]
        if mins != sorted(mins):
            raise ClefsError(400, "Ladder steps must be in time order", "bad_request")


@router.get("/sla-policies")
def get_sla_policies(slug: str, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return list_sla_policies(cur, slug)


@router.put("/sla-policies")
def put_sla_policies(slug: str, body: SlaPoliciesIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> list[dict]:
    with db.db() as cur:
        for p in body.policies:
            _check_scope(cur, slug, p)
        before = [r for r in list_sla_policies(cur, slug) if r["source"] == "hotel"]
        for k in body.reset:
            cur.execute("DELETE FROM svc_sla_policies WHERE hotel_slug = %s AND scope_type = %s AND scope_key = %s",
                        (slug, k.scope_type, k.scope_key))
        for p in body.policies:
            cur.execute(
                """INSERT INTO svc_sla_policies (hotel_slug, scope_type, scope_key, ack_min, resolve_min, due_local, ladder,
                       conditions, active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (hotel_slug, scope_type, scope_key) DO UPDATE SET ack_min = EXCLUDED.ack_min,
                       resolve_min = EXCLUDED.resolve_min, due_local = EXCLUDED.due_local, ladder = EXCLUDED.ladder,
                       conditions = EXCLUDED.conditions, active = EXCLUDED.active, updated_at = NOW()""",
                (slug, p.scope_type, p.scope_key, p.ack_min, p.resolve_min, p.due_local,
                 Json([s.model_dump() for s in p.ladder]) if p.ladder else None, Json(p.conditions), p.active),
            )
        after = list_sla_policies(cur, slug)
        audit(cur, ctx, "service.config.sla_policies", "svc_sla_policies", slug, before={"rows": before},
              after={"rows": [r for r in after if r["source"] == "hotel"]}, hotel_slug=slug)
        return after

# ─── Compensation authority ───
# Values travel as Decimal strings in the hotel's currency and are stored as
# minor units + CHAR(3) (Canon 2). max_value null means unlimited.


class AuthorityIn(BaseModel):
    role_key: Literal["staff", "supervisor", "duty_manager", "gm"]
    max_value: Decimal | None = Field(None, ge=0)
    allowed_kinds: list[Literal["amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount", "other"]]


class CompensationIn(BaseModel):
    roles: list[AuthorityIn] = Field(default_factory=list, max_length=4)
    reset: list[Literal["staff", "supervisor", "duty_manager", "gm"]] = Field(default_factory=list)


def _matrix_out(cur: Any, hotel: Any) -> dict:
    from clefs.modules.service import recovery

    m = recovery.authority_matrix(cur, hotel)
    return {"currency": money.ccy(hotel.currency or "AED"), "kinds": list(C.GESTURE_KINDS),
            "roles": [m[r] for r in C.AUTHORITY_ROLES]}


@router.get("/compensation")
def get_compensation(slug: str, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> dict:
    with db.db() as cur:
        return _matrix_out(cur, get_hotel(cur, slug))


@router.put("/compensation")
def put_compensation(slug: str, body: CompensationIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    """A department head may not widen the matrix: raising any limit above
    the current one needs an owner or GM (core.settings.manage)."""
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        before = _matrix_out(cur, hotel)
        ccy = before["currency"]
        current = {r["role_key"]: r for r in before["roles"]}
        for r in body.roles:
            new_max = None if r.max_value is None else money.to_minor(r.max_value, ccy)
            old = current[r.role_key]
            wider = (old["max_minor"] is not None and (new_max is None or new_max > old["max_minor"])) or bool(
                set(r.allowed_kinds) - set(old["allowed_kinds"]))
            if wider and not ctx.can("core.settings.manage"):
                raise ClefsError(403, "Only an owner or GM can raise compensation authority", "forbidden")
        if body.reset and not ctx.can("core.settings.manage"):
            raise ClefsError(403, "Only an owner or GM can reset compensation authority", "forbidden")
        _reset(cur, "svc_compensation_authority", slug, list(body.reset), key_col="role_key")
        for r in body.roles:
            cur.execute(
                """INSERT INTO svc_compensation_authority (hotel_slug, role_key, max_minor, currency, allowed_kinds)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (hotel_slug, role_key) DO UPDATE SET max_minor = EXCLUDED.max_minor,
                       currency = EXCLUDED.currency, allowed_kinds = EXCLUDED.allowed_kinds, updated_at = NOW()""",
                (slug, r.role_key, None if r.max_value is None else money.to_minor(r.max_value, ccy), ccy,
                 sorted(set(r.allowed_kinds))),
            )
        after = _matrix_out(cur, hotel)
        audit(cur, ctx, "service.config.compensation", "svc_compensation_authority", slug, before=before, after=after,
              hotel_slug=slug)
        return after


# ─── Complaint categories ───


@router.get("/complaint-categories")
def get_categories(slug: str, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        out = {k: {"key": k, **v, "active": True, "source": "default"} for k, v in C.COMPLAINT_CATEGORIES.items()}
        cur.execute("SELECT key, label_en, label_ar, department_key, active FROM svc_complaint_categories WHERE hotel_slug = %s",
                    (slug,))
        for r in cur.fetchall():
            out[r["key"]] = {**dict(r), "source": "hotel"}
        return sorted(out.values(), key=lambda c: c["key"])


class CategoryIn(BaseModel):
    key: str = Field(..., pattern=KEY_PATTERN)
    label_en: str = Field(..., min_length=1, max_length=80)
    label_ar: str | None = Field(None, max_length=80)
    department_key: str | None = Field(None, pattern=KEY_PATTERN)
    active: bool = True


class CategoriesIn(BaseModel):
    categories: list[CategoryIn] = Field(default_factory=list, max_length=100)
    reset: list[str] = Field(default_factory=list, max_length=100)


@router.put("/complaint-categories")
def put_categories(slug: str, body: CategoriesIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> list[dict]:
    with db.db() as cur:
        _reset(cur, "svc_complaint_categories", slug, body.reset)
        for c in body.categories:
            cur.execute(
                """INSERT INTO svc_complaint_categories (hotel_slug, key, label_en, label_ar, department_key, active)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (hotel_slug, key) DO UPDATE SET label_en = EXCLUDED.label_en, label_ar = EXCLUDED.label_ar,
                       department_key = EXCLUDED.department_key, active = EXCLUDED.active""",
                (slug, c.key, c.label_en, c.label_ar, c.department_key, c.active),
            )
        audit(cur, ctx, "service.config.complaint_categories", "svc_complaint_categories", slug,
              after={"keys": [c.key for c in body.categories], "reset": body.reset}, hotel_slug=slug)
    return get_categories(slug, ctx)

# ─── Mounting ───


def ops_routers() -> list[APIRouter]:
    """Every router of the ops part, in mount order. Imported lazily so this
    file can be imported by the others without a cycle.

    FOUNDATION-REQUEST (service manifest owner): module.py should list
    `*config_api.ops_routers()` INSTEAD of router_public and router_device:
    public_api and devices_api serve the same paths (/public/r/{token},
    /confirm, /device/service/feed) with the same shapes plus more, and
    mounting both would be a duplicate route (test_module_contracts)."""
    from clefs.modules.service import (analytics, devices_api, handover, incidents, public_api, recovery,
                                       roster)

    return [router, roster.router, handover.router, incidents.router, recovery.router, analytics.router,
            public_api.router, devices_api.router]


def mount_ops(app: Any) -> None:
    """Mount the ops routers on an app whose manifest does not list them yet
    (tests, and any deploy before the manifest wiring lands). A route the
    app already serves at the same path and method is replaced in place, so
    the superset handler wins; calling it twice changes nothing."""
    if getattr(app.state, "service_ops_mounted", False):
        return
    tmp = APIRouter()
    for r in ops_routers():
        tmp.include_router(r)
    ours = {(r.path, frozenset(getattr(r, "methods", None) or ())): r for r in tmp.routes}
    routes = app.router.routes
    for i, existing in enumerate(list(routes)):
        key = (getattr(existing, "path", None), frozenset(getattr(existing, "methods", None) or ()))
        if key in ours:
            routes[i] = ours.pop(key)
    routes.extend(ours.values())
    app.state.service_ops_mounted = True


# Registers the WhatsApp fallback handler (priority 90) and the tracker push
# job: the manifest reaches this file, so importing it here wires them.
from clefs.modules.service import whatsapp as _whatsapp  # noqa: E402,F401
