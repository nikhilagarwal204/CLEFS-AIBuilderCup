"""
Shift templates and the roster (03 spec 1.6). Service owns who works when,
where and in which shift role; the foundation owns devices, PINs and the
device_sessions a person opens when they actually start (Canon 5).

- Templates are seeded per hotel on first read from constants.SHIFT_TEMPLATES
  (UAE defaults: morning 07-15, evening 15-23, night 23-07, hk_day 08-17,
  hk_eve 15-24), then owned by the hotel.
- An assignment is one person on one business date: template times (or
  explicit HH:MM) become TIMESTAMPTZ in the hotel's timezone, so a night
  shift ends the next local morning and DST never matters to the caller.
- PUT of a week replaces that week: rows left out are cancelled (kept, for
  the audit trail and for analytics of what was planned).
- Every save runs labour.check over the week plus a week either side (a
  seventh day in a row across a week boundary is still caught). A midday-ban
  hit blocks the save unless the row carries override_reason, which is
  written to the audit log.
- The escalation ladder and routing read shift_role and floors from here
  (supervisor / duty manager on shift now).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm, require_perm_or_device
from clefs.core import clock
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.service import constants as C
from clefs.modules.service import labour

# ─── Templates ───

TEMPLATE_KEYS = ("id", "key", "name", "department_key", "start_time", "end_time", "crosses_midnight", "break_min", "active")


def _crosses(start: time, end: time) -> bool:
    return end <= start


def ensure_templates(cur: Any, hotel_slug: str) -> None:
    """Seed the UAE defaults once per hotel (ON CONFLICT keeps edits)."""
    cur.execute("SELECT 1 FROM svc_shift_templates WHERE hotel_slug = %s LIMIT 1", (hotel_slug,))
    if cur.fetchone():
        return
    for t in C.SHIFT_TEMPLATES:
        cur.execute(
            """INSERT INTO svc_shift_templates (hotel_slug, key, name, department_key, start_time, end_time,
                   crosses_midnight, break_min) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (hotel_slug, key) DO NOTHING""",
            (hotel_slug, t["key"], t["name"], t["department_key"], t["start_time"], t["end_time"],
             _crosses(t["start_time"], t["end_time"]), t["break_min"]),
        )


def template_out(r: dict) -> dict:
    out = {k: r.get(k) for k in TEMPLATE_KEYS}
    out["start_time"] = r["start_time"].strftime("%H:%M")
    out["end_time"] = r["end_time"].strftime("%H:%M")
    return out


def list_templates(cur: Any, hotel_slug: str, *, active_only: bool = False) -> list[dict]:
    ensure_templates(cur, hotel_slug)
    cur.execute(
        "SELECT * FROM svc_shift_templates WHERE hotel_slug = %s" + (" AND active" if active_only else "")
        + " ORDER BY start_time, key",
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


class TemplateIn(BaseModel):
    key: str = Field(..., pattern=r"^[a-z0-9_]{1,40}$")
    name: str = Field(..., min_length=1, max_length=80)
    department_key: str | None = Field(None, max_length=40)
    start_time: time
    end_time: time
    break_min: int = Field(0, ge=0, le=240)
    active: bool = True


def save_templates(cur: Any, hotel_slug: str, items: list[TemplateIn], *, actor: Any) -> list[dict]:
    """Upsert by key. Templates are never deleted (assignments point at
    them); switch one off with active=false."""
    ensure_templates(cur, hotel_slug)
    before = [template_out(r) for r in list_templates(cur, hotel_slug)]
    for t in items:
        cur.execute(
            """INSERT INTO svc_shift_templates (hotel_slug, key, name, department_key, start_time, end_time,
                   crosses_midnight, break_min, active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (hotel_slug, key) DO UPDATE SET name = EXCLUDED.name,
                   department_key = EXCLUDED.department_key, start_time = EXCLUDED.start_time,
                   end_time = EXCLUDED.end_time, crosses_midnight = EXCLUDED.crosses_midnight,
                   break_min = EXCLUDED.break_min, active = EXCLUDED.active""",
            (hotel_slug, t.key, t.name, (C.department_key(t.department_key) or t.department_key) if t.department_key else None,
             t.start_time, t.end_time, _crosses(t.start_time, t.end_time), t.break_min, t.active),
        )
    after = [template_out(r) for r in list_templates(cur, hotel_slug)]
    audit(cur, actor, "service.shift_templates.update", "svc_shift_templates", hotel_slug,
          before={"templates": before}, after={"templates": after}, hotel_slug=hotel_slug)
    return after


# ─── Assignments ───

ASSIGNMENT_KEYS = ("id", "staff_member_id", "template_id", "business_date", "start_at", "end_at", "department_key",
                   "floors", "shift_role", "status", "labour_flags", "override_reason")


def week_bounds(d: date) -> tuple[date, date]:
    """Monday..Sunday of the ISO week holding d (business dates)."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


class AssignmentIn(BaseModel):
    id: int | None = None  # an existing row to update
    key: str | None = Field(None, max_length=60)  # the client's cell id, echoed in flag refs
    staff_member_id: int
    business_date: date
    template_id: int | None = None
    template_key: str | None = Field(None, max_length=40)
    # explicit local times (HH:MM) instead of, or overriding, the template
    start_time: time | None = None
    end_time: time | None = None
    # or the instants GET /roster returns (the console round-trips them);
    # read in the hotel's timezone, after start_time/end_time and before the
    # template's times
    start_at: datetime | None = None
    end_at: datetime | None = None
    break_min: int | None = Field(None, ge=0, le=240)
    department_key: str | None = Field(None, max_length=40)
    floors: list[str] = Field(default_factory=list, max_length=50)
    shift_role: Literal["attendant", "supervisor", "duty_manager"] = "attendant"
    status: Literal["planned", "cancelled"] = "planned"
    override_reason: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _times(self) -> "AssignmentIn":
        if not (self.template_id or self.template_key) and not (self.start_time and self.end_time) \
                and not (self.start_at and self.end_at):
            raise ValueError("give a template, both start_time and end_time, or both start_at and end_at")
        return self


def assignment_out(r: dict, tz: Any = None) -> dict:
    """Instants in the hotel's timezone (the DB session's zone is not ours)."""
    out = {k: r.get(k) for k in ASSIGNMENT_KEYS}
    out["business_date"] = r["business_date"].isoformat()
    out["start_at"] = (r["start_at"].astimezone(tz) if tz else r["start_at"]).isoformat()
    out["end_at"] = (r["end_at"].astimezone(tz) if tz else r["end_at"]).isoformat()
    for k in ("staff_name", "template_key", "is_outdoor_role", "break_min"):
        if k in r:
            out[k] = r[k]
    return out


def materialize(hotel: Any, a: AssignmentIn, templates: dict[str, dict]) -> dict:
    """Template or explicit local times -> an aware [start_at, end_at). An end
    at or before the start is the next local day (night shifts, hk_eve's
    midnight end)."""
    t = templates.get(f"id:{a.template_id}") if a.template_id else templates.get(f"key:{a.template_key}")
    if (a.template_id or a.template_key) and t is None:
        raise ClefsError(400, "Unknown shift template", "unknown_template")
    tz = htime.tz_of(hotel)

    def local(x: datetime | None) -> time | None:  # a naive instant is already hotel-local
        return None if x is None else (x if x.tzinfo is None else x.astimezone(tz)).time().replace(tzinfo=None)

    start_t = a.start_time or local(a.start_at) or t["start_time"]
    end_t = a.end_time or local(a.end_at) or t["end_time"]
    start = datetime.combine(a.business_date, start_t, tzinfo=tz)
    end = datetime.combine(a.business_date, end_t, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    dept = a.department_key or (t or {}).get("department_key")
    return {
        "id": a.id, "ref": a.key or (f"id:{a.id}" if a.id else None), "staff_member_id": a.staff_member_id,
        "template_id": t["id"] if t else None, "business_date": a.business_date, "start_at": start, "end_at": end,
        "break_min": a.break_min if a.break_min is not None else (t["break_min"] if t else 0),
        "break_min_set": a.break_min,  # stored only when typed (NULL = the template's)
        "department_key": (C.department_key(dept) or dept) if dept else None,
        "floors": [str(f).strip() for f in a.floors if str(f).strip()], "shift_role": a.shift_role,
        "status": a.status, "override_reason": (a.override_reason or "").strip() or None,
    }


def _template_index(cur: Any, hotel_slug: str) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for r in list_templates(cur, hotel_slug):
        idx[f"id:{r['id']}"] = r
        idx[f"key:{r['key']}"] = r
    return idx


def list_assignments(cur: Any, hotel_slug: str, start: date, end: date, *, include_cancelled: bool = False,
                     staff_ids: list[int] | None = None) -> list[dict]:
    """Assignments with business_date in [start, end], with the staff name,
    outdoor flag and the template's break (paid hours need it)."""
    sql = ["""SELECT a.*, s.name AS staff_name, s.is_outdoor_role, t.key AS template_key,
                     COALESCE(a.break_min, t.break_min, 0) AS break_min
              FROM svc_shift_assignments a JOIN staff_members s ON s.id = a.staff_member_id
              LEFT JOIN svc_shift_templates t ON t.id = a.template_id
              WHERE a.hotel_slug = %s AND a.business_date BETWEEN %s AND %s"""]
    params: list[Any] = [hotel_slug, start, end]
    if not include_cancelled:
        sql.append("AND a.status = 'planned'")
    if staff_ids is not None:
        sql.append("AND a.staff_member_id = ANY(%s)")
        params.append(staff_ids)
    sql.append("ORDER BY a.business_date, a.start_at, s.name")
    cur.execute(" ".join(sql), params)
    return [dict(r) for r in cur.fetchall()]

# ─── Validation and save ───

CONTEXT_DAYS = 7  # a week either side, so runs and weeks across the edge are seen


def _staff_rows(cur: Any, hotel_slug: str, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    cur.execute("SELECT id, name, is_outdoor_role FROM staff_members WHERE hotel_slug = %s AND id = ANY(%s)",
                (hotel_slug, ids))
    return {r["id"]: dict(r) for r in cur.fetchall()}


def _stored_as_check(r: dict) -> dict:
    return {"id": r["id"], "ref": f"id:{r['id']}", "staff_member_id": r["staff_member_id"],
            "business_date": r["business_date"], "start_at": r["start_at"], "end_at": r["end_at"],
            "break_min": r.get("break_min") or 0, "is_outdoor": bool(r.get("is_outdoor_role")),
            "override_reason": r.get("override_reason"), "status": r["status"]}


def validate(cur: Any, hotel: Any, items: list[AssignmentIn], *, week: tuple[date, date] | None = None,
             replace_week: bool = False) -> tuple[list[dict], list[dict]]:
    """Materialize `items` and run the labour rules over them plus the stored
    neighbours. With replace_week, stored rows of `week` that are not in
    `items` count as gone (the PUT semantics). -> (rows, flags)."""
    templates = _template_index(cur, hotel.slug)
    rows = [materialize(hotel, a, templates) for a in items]
    staff = _staff_rows(cur, hotel.slug, sorted({r["staff_member_id"] for r in rows}))
    missing = {r["staff_member_id"] for r in rows} - set(staff)
    if missing:
        raise ClefsError(404, f"Unknown staff member {sorted(missing)[0]}", "staff_not_found")
    for r in rows:
        r["is_outdoor"] = bool(staff[r["staff_member_id"]]["is_outdoor_role"])
    dates = [r["business_date"] for r in rows] + (list(week) if week else [])
    if not dates:
        return rows, []
    lo, hi = min(dates), max(dates)
    ids = {r["id"] for r in rows if r["id"]}
    stored = list_assignments(cur, hotel.slug, lo - timedelta(days=CONTEXT_DAYS), hi + timedelta(days=CONTEXT_DAYS),
                              staff_ids=sorted(staff))
    context = []
    for s in stored:
        if s["id"] in ids:
            continue
        if replace_week and week and week[0] <= s["business_date"] <= week[1]:
            continue
        context.append(_stored_as_check(s))
    rules = labour.load_rules(cur, hotel.slug)
    focus = week or (lo, hi)
    flags = labour.check(rows + context, rules, tz=htime.tz_of(hotel), focus=focus)
    return rows, flags


def _flags_for(ref: Any, flags: list[dict]) -> list[dict]:
    return [{k: f[k] for k in ("code", "level", "message", "business_date")} for f in flags if ref in f["refs"]]


def save_week(cur: Any, hotel: Any, week_of: date, items: list[AssignmentIn], *, actor: Any) -> dict:
    """Replace the roster of the ISO week holding `week_of`. 409
    labour_blocked (with the flags) when a midday-ban hit has no reason;
    every override reason goes to the audit log."""
    week = week_bounds(week_of)
    outside = [a for a in items if not week[0] <= a.business_date <= week[1]]
    if outside:
        raise ClefsError(400, "Every assignment must fall in the week being saved", "outside_week")
    rows, flags = validate(cur, hotel, items, week=week, replace_week=True)
    check_shift_role_authority(cur, hotel.slug, rows, actor)  # before labour: a grant nobody may make is not a 409
    blocked = labour.blocking(flags)
    if blocked:
        raise LabourBlocked(blocked, flags)
    tz = htime.tz_of(hotel)
    before = [assignment_out(r, tz) for r in list_assignments(cur, hotel.slug, *week)]
    keep: list[int] = []
    for r in rows:
        r["labour_flags"] = _flags_for(r["ref"], flags) if r["ref"] else []
        keep.append(_upsert(cur, hotel.slug, r, actor))
    cur.execute(
        """UPDATE svc_shift_assignments SET status = 'cancelled', updated_at = NOW()
           WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s AND status = 'planned' AND NOT (id = ANY(%s))""",
        (hotel.slug, week[0], week[1], keep),
    )
    after = [assignment_out(r, tz) for r in list_assignments(cur, hotel.slug, *week)]
    audit(cur, actor, "service.roster.save", "svc_shift_assignments", f"{hotel.slug}:{week[0].isoformat()}",
          before={"n": len(before)}, after={"n": len(after), "ids": keep}, hotel_slug=hotel.slug)
    for f in flags:
        if f["level"] == "overridden":
            audit(cur, actor, "service.roster.labour_override", "svc_shift_assignments", str(f["staff_member_id"]),
                  after={"code": f["code"], "business_date": f["business_date"], "reason": f.get("override_reason")},
                  hotel_slug=hotel.slug)
    return {"week_start": week[0].isoformat(), "week_end": week[1].isoformat(), "assignments": after,
            "flags": flags, "caption": labour.CAPTION}


class LabourBlocked(ClefsError):
    def __init__(self, blocked: list[dict], flags: list[dict]):
        super().__init__(409, blocked[0]["message"] + ": give an override reason to save", "labour_blocked")
        self.flags = flags


# ─── Shift-role authority ───
# A supervisor or duty-manager shift lifts a device user's compensation
# authority (recovery.authority_role reads it), so putting someone on one is
# itself a grant of authority. put_compensation already stops a department
# head from widening the matrix; without this cap the same person (or the
# front desk, who also holds service.manage) could route around it by rostering
# any staff record as duty manager, setting its PIN and pairing a tablet. A
# caller may therefore assign a shift role no higher than their own authority
# (MEMBERSHIP_AUTHORITY: dept_head -> supervisor, front desk -> attendant);
# owner, GM (core.settings.manage) and system callers are uncapped.

SHIFT_ROLE_RANK = {"attendant": 0, "supervisor": 1, "duty_manager": 2}
AUTHORITY_RANK = {"staff": 0, "supervisor": 1, "duty_manager": 2, "gm": 3}


def assignable_rank(actor: Any) -> int:
    """The highest SHIFT_ROLE_RANK `actor` may assign (3 = any)."""
    if actor is None or not hasattr(actor, "can"):
        return 3  # an internal caller (seed, jobs): no console principal to cap
    p = getattr(actor, "principal", None)
    if getattr(p, "kind", None) == "service" or actor.can("core.settings.manage"):
        return 3
    return AUTHORITY_RANK.get(C.MEMBERSHIP_AUTHORITY.get(getattr(actor, "role", None) or "staff", "staff"), 0)


def check_shift_role_authority(cur: Any, hotel_slug: str, rows: list[dict], actor: Any) -> None:
    """403 shift_role_forbidden when a row newly grants a shift role above
    the caller's authority. A stored elevated row the caller re-sends
    untouched (same person, role and times) passes, so a department head can
    still save a week in which the GM put a duty manager; cancelling or
    demoting an elevated row never needs authority (it only takes it away)."""
    cap = assignable_rank(actor)
    over = [r for r in rows if r["status"] == "planned" and SHIFT_ROLE_RANK.get(r["shift_role"], 0) > cap]
    if not over:
        return
    ids = [r["id"] for r in over if r["id"]]
    stored: dict[int, dict] = {}
    if ids:
        cur.execute("""SELECT id, staff_member_id, shift_role, start_at, end_at, status FROM svc_shift_assignments
                       WHERE hotel_slug = %s AND id = ANY(%s)""", (hotel_slug, ids))
        stored = {s["id"]: dict(s) for s in cur.fetchall()}
    for r in over:
        s = stored.get(r["id"]) if r["id"] else None
        untouched = s is not None and s["status"] == "planned" and s["staff_member_id"] == r["staff_member_id"] \
            and s["shift_role"] == r["shift_role"] and s["start_at"] == r["start_at"] and s["end_at"] == r["end_at"]
        if not untouched:
            label = r["shift_role"].replace("_", " ")
            raise ClefsError(403, f"Only an owner or GM can put someone on a {label} shift", "shift_role_forbidden")


def _upsert(cur: Any, hotel_slug: str, r: dict, actor: Any) -> int:
    vals = (r["staff_member_id"], r["template_id"], r["business_date"], r["start_at"], r["end_at"],
            r["department_key"], r["floors"], r["shift_role"], r["status"], Json(r["labour_flags"]),
            r["override_reason"], r["break_min_set"])
    if r["id"]:
        cur.execute(
            """UPDATE svc_shift_assignments SET staff_member_id = %s, template_id = %s, business_date = %s,
                   start_at = %s, end_at = %s, department_key = %s, floors = %s, shift_role = %s, status = %s,
                   labour_flags = %s, override_reason = %s, break_min = %s, updated_at = NOW()
               WHERE id = %s AND hotel_slug = %s RETURNING id""",
            (*vals, r["id"], hotel_slug),
        )
        row = cur.fetchone()
        if row is None:
            raise ClefsError(404, "Assignment not found", "assignment_not_found")
        return row["id"]
    cur.execute(
        """INSERT INTO svc_shift_assignments (staff_member_id, template_id, business_date, start_at, end_at,
               department_key, floors, shift_role, status, labour_flags, override_reason, break_min, hotel_slug,
               created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (*vals, hotel_slug, getattr(getattr(actor, "principal", actor), "user_id", None)),
    )
    return cur.fetchone()["id"]

# ─── Who is on shift ───


def on_shift(cur: Any, hotel_slug: str, at: datetime | None = None, *, role: str | None = None,
             department_key: str | None = None) -> list[dict]:
    """Planned assignments covering `at` (default now), with the person.
    role: attendant | supervisor | duty_manager."""
    at = at or clock.now()
    sql = ["""SELECT a.*, s.name AS staff_name, s.user_id, s.department AS staff_department, s.role_key
              FROM svc_shift_assignments a JOIN staff_members s ON s.id = a.staff_member_id
              WHERE a.hotel_slug = %s AND a.status = 'planned' AND a.start_at <= %s AND a.end_at > %s
                AND COALESCE(s.is_active, TRUE)"""]
    params: list[Any] = [hotel_slug, at, at]
    if role:
        sql.append("AND a.shift_role = %s")
        params.append(role)
    if department_key:
        sql.append("AND a.department_key = %s")
        params.append(department_key)
    sql.append("ORDER BY a.start_at, s.name")
    cur.execute(" ".join(sql), params)
    return [dict(r) for r in cur.fetchall()]


def on_duty(cur: Any, hotel_slug: str, *, role: str, at: datetime) -> list[dict]:
    """api.on_duty's body: who holds `role` at `at` -> [{user_id,
    staff_member_id, name}]. A shift role comes from the roster; "gm" (not a
    shift role) is the hotel's active GM memberships."""
    if role in C.SHIFT_ROLES:
        return [{"user_id": r["user_id"], "staff_member_id": r["staff_member_id"], "name": r["staff_name"]}
                for r in on_shift(cur, hotel_slug, at, role=role)]
    cur.execute(
        """SELECT u.id AS user_id, u.name FROM hotel_memberships m JOIN users u ON u.id = m.user_id
           WHERE m.hotel_slug = %s AND m.role = %s AND m.is_active ORDER BY u.id""",
        (hotel_slug, role),
    )
    return [{"user_id": r["user_id"], "staff_member_id": None, "name": r["name"]} for r in cur.fetchall()]


def shift_role_of(cur: Any, hotel_slug: str, staff_member_id: int | None, at: datetime | None = None) -> str | None:
    """The shift role a person holds right now (the highest if two overlap)."""
    if not staff_member_id:
        return None
    rank = {"attendant": 0, "supervisor": 1, "duty_manager": 2}
    rows = [r for r in on_shift(cur, hotel_slug, at) if r["staff_member_id"] == staff_member_id]
    return max((r["shift_role"] for r in rows), key=lambda x: rank.get(x, 0), default=None)


def current_assignment(cur: Any, hotel_slug: str, staff_member_id: int, at: datetime | None = None) -> dict | None:
    rows = [r for r in on_shift(cur, hotel_slug, at) if r["staff_member_id"] == staff_member_id]
    return rows[0] if rows else None


# ─── Routes ───

router = APIRouter(prefix="/hotels/{slug}/service", tags=["service-roster"])


def _week_of(week: date | None, hotel: Any) -> date:
    return week or htime.business_date(hotel)


@router.get("/shift-templates")
def get_templates(slug: str, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db() as cur:
        return [template_out(r) for r in list_templates(cur, slug)]


class TemplatesIn(BaseModel):
    templates: list[TemplateIn] = Field(..., max_length=50)


@router.put("/shift-templates")
def put_templates(slug: str, body: TemplatesIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> list[dict]:
    with db.db() as cur:
        return save_templates(cur, slug, body.templates, actor=ctx)


@router.get("/roster")
def get_roster(slug: str, week: date | None = Query(None, description="any day of the ISO week"),
               ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> dict:
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        lo, hi = week_bounds(_week_of(week, hotel))
        rows = list_assignments(cur, slug, lo, hi)
        stored = [_stored_as_check(r) for r in list_assignments(cur, slug, lo - timedelta(days=CONTEXT_DAYS),
                                                                     hi + timedelta(days=CONTEXT_DAYS))]
        flags = labour.check(stored, labour.load_rules(cur, slug), tz=htime.tz_of(hotel), focus=(lo, hi))
        return {"week_start": lo.isoformat(), "week_end": hi.isoformat(),
                "assignments": [assignment_out(r, htime.tz_of(hotel)) for r in rows], "templates": [template_out(t) for t in list_templates(cur, slug)],
                "flags": flags, "caption": labour.CAPTION}


class RosterIn(BaseModel):
    week: date
    assignments: list[AssignmentIn] = Field(default_factory=list, max_length=2000)


@router.put("/roster")
def put_roster(slug: str, body: RosterIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> Any:
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
        try:
            return save_week(cur, hotel, body.week, body.assignments, actor=ctx)
        except LabourBlocked as exc:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=409, content={"detail": exc.detail, "code": exc.code, "flags": exc.flags,
                                                          "caption": labour.CAPTION})


@router.post("/roster/validate")
def validate_roster(slug: str, body: RosterIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    """The same checks as a save, nothing written (the scheduler calls it as
    cells change)."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        week = week_bounds(body.week)
        _, flags = validate(cur, hotel, body.assignments, week=week, replace_week=True)
        return {"week_start": week[0].isoformat(), "week_end": week[1].isoformat(), "flags": flags,
                "blocked": bool(labour.blocking(flags)), "caption": labour.CAPTION}


@router.get("/labour-rules")
def get_labour_rules(slug: str, ctx: HotelCtx = Depends(require_perm("service.view"))) -> dict:
    with db.db(readonly=True) as cur:
        return labour.load_rules(cur, slug).out()


@router.put("/labour-rules")
def put_labour_rules(slug: str, body: labour.LabourRules, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    with db.db() as cur:
        return labour.save_rules(cur, slug, body, actor=ctx).out()
