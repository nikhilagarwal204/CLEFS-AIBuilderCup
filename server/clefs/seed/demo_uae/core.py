"""
Demo part "core": the property and its people (02 spec 9, "Property").

Al Rigga Crest Hotel: Deira, 3-star, 80 rooms on 8 floors of 10
(40 STD_KING, 30 STD_TWIN, 10 FAM_2BR with two bedrooms), Dubai tax profile
hotel_3 convention A, the OTA and direct channels, Service departments, 18
staff with PINs, six console users (owner, GM, finance, revenue, front desk
and the duty manager) and one paired front-desk device.

The deck cast (05 spec section 4) lives here too: Room 204 on floor 2,
Maria Santos (Housekeeping attendant, floors 1-4, reads Tagalog), Priya
Sharma (Housekeeping attendant, floors 5-8), the Housekeeping supervisor,
the duty manager and the GM. The deck part (deck.py) puts them on shift for
the next 14 days and pairs Maria's phone; /try (clefs.core.demo_access)
signs reviewers in as them.

Everything goes through core services (rooms, tax, devices, stays
channels). The only raw writes are core tables without a writer function
(channels' display names, users and memberships, staff_members) and the one
Service table the console writes directly (svc_departments).
"""

from __future__ import annotations

import secrets
from datetime import date
from types import SimpleNamespace
from typing import Any

from clefs.auth import devices, passwords
from clefs.core import rooms, tax

PART_ORDER = 10
ACTOR = SimpleNamespace(kind="system", user_id=None, device_id=None, label="demo-seed")

# ─── The deck cast (05 spec section 4) ───
# /try (clefs.core.demo_access), the deck part and docs/DEMO.md name these
# people; a rename here is a rename there.
DECK_ROOM = "204"
DECK_ATTENDANT = "Maria Santos"
# Fixed so /try can print it: it opens Maria's shift only on a phone already
# paired to a demo hotel, which only /try or a manager can pair.
DECK_ATTENDANT_PIN = "2046"
DECK_SECOND_ATTENDANT = "Priya Sharma"
DECK_SUPERVISOR = "Rekha Nair"
DECK_DUTY_MANAGER = "Omar Khalid"
DECK_GM = "Sarah Thompson"
# The console login /try signs reviewers in with (dept_head at this hotel only).
DUTY_MANAGER_LOCAL = "dutymanager"
DUTY_MANAGER_ROLE = "dept_head"

# ─── Rooms ───

FLOORS = range(1, 9)
ROOM_TYPES = (
    {"code": "STD_KING", "name": "Standard King", "bedrooms": 1, "max_occupancy": 2, "room_count": 40},
    {"code": "STD_TWIN", "name": "Standard Twin", "bedrooms": 1, "max_occupancy": 2, "room_count": 30},
    {"code": "FAM_2BR", "name": "Family Two-Bedroom", "bedrooms": 2, "max_occupancy": 5, "room_count": 10},
)


def room_type_for(floor: int, pos: int) -> str:
    """x01-x05 kings on every floor (40); the family units are x10 on floors
    1-6 plus x09 and x10 on 7-8 (10); every other room is a twin (30)."""
    if pos <= 5:
        return "STD_KING"
    if pos == 10 or (floor >= 7 and pos == 9):
        return "FAM_2BR"
    return "STD_TWIN"


def room_plan() -> list[dict]:
    out = []
    for f in FLOORS:
        for pos in range(1, 11):
            number = f"{f}{pos:02d}"
            code = room_type_for(f, pos)
            out.append({
                "room_number": number,
                "floor": str(f),
                # Odd rooms face the creek side; the wing is what the
                # housekeeping board groups by.
                "wing": "Creek" if pos % 2 else "Rigga",
                "room_type_code": code,
                "bedrooms": 2 if code == "FAM_2BR" else 1,
                # Room phones dial 3 + room number (a common Deira PABX plan).
                "extension": f"3{number}",
                "service_state": "in_service",
                "is_active": True,
            })
    return out


def seed_rooms(ctx: Any) -> dict:
    rooms.upsert_room_types(ctx.cur, ctx.slug, [dict(t) for t in ROOM_TYPES])
    plan = room_plan()
    rooms.upsert_rooms(ctx.cur, ctx.slug, plan)
    ctx.ids["rooms"] = {r["room_number"]: r for r in plan}
    return {"room_types": len(ROOM_TYPES), "rooms": len(plan)}


# ─── Tax ───

DEMO_TRN = "100234567800003"  # 15 digits, the UAE TRN shape; not a real registration


def seed_tax(ctx: Any) -> dict:
    """Dated from 2024-01-01 so the 2024-2025 baselines and every history
    night resolve to hotel_3 convention A (02 spec 9)."""
    tax.set_tax_profile(ctx.cur, ctx.slug, effective_from=date(2024, 1, 1), property_category="hotel_3",
                        convention="A", trn=DEMO_TRN)
    return {"tax_profile": "hotel_3/A"}


# ─── Channels ───

# code, display name, kind, default collect model. Booking invoices its
# commission monthly (hotel collect); Agoda and Expedia collect and pay out
# (Expedia also sells Hotel Collect, 02 spec 9 contracts).
CHANNELS = (
    ("BOOKING", "Booking.com", "ota", "HOTEL_COLLECT"),
    ("AGODA", "Agoda", "ota", "OTA_COLLECT"),
    ("EXPEDIA", "Expedia", "ota", "OTA_COLLECT"),
    ("DIRECT_WEB", "Direct (website)", "direct", "HOTEL_COLLECT"),
    ("DIRECT_PHONE", "Direct (phone and email)", "direct", "HOTEL_COLLECT"),
    ("CORPORATE", "Corporate accounts", "corporate", "HOTEL_COLLECT"),
    ("WALKIN", "Walk-in", "walk_in", "HOTEL_COLLECT"),
)


def seed_channels(ctx: Any) -> dict:
    for code, name, kind, collect in CHANNELS:
        ctx.cur.execute(
            """
            INSERT INTO channels (hotel_slug, code, name, kind, default_collect_model) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, code) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
                default_collect_model = EXCLUDED.default_collect_model, is_active = TRUE
            """,
            (ctx.slug, code, name, kind, collect),
        )
    return {"channels": len(CHANNELS)}


# ─── Departments ───

# Service's default keys (service/constants.DEPARTMENTS), written as the
# hotel's own rows so the demo console shows them as configured.
DEPARTMENTS = (
    ("housekeeping", "Housekeeping", "التدبير المنزلي", "Housekeeping", "#3b82f6", 10),
    ("ird", "In-Room Dining", "خدمة الطعام في الغرف", "Food & Beverage", "#f59e0b", 20),
    ("engineering", "Engineering", "الهندسة والصيانة", "Maintenance", "#ef4444", 30),
    ("front_office", "Front Office", "مكتب الاستقبال", "Front Desk", "#8b5cf6", 40),
)


def seed_departments(ctx: Any) -> dict:
    if not ctx.table_exists("svc_departments"):
        return {"departments": 0}
    for key, name, name_ar, legacy, color, sort in DEPARTMENTS:
        ctx.cur.execute(
            """
            INSERT INTO svc_departments (hotel_slug, key, name, name_ar, legacy_category, color, active, sort)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
            ON CONFLICT (hotel_slug, key) DO UPDATE SET name = EXCLUDED.name, name_ar = EXCLUDED.name_ar,
                legacy_category = EXCLUDED.legacy_category, color = EXCLUDED.color, active = TRUE,
                sort = EXCLUDED.sort, updated_at = NOW()
            """,
            (ctx.slug, key, name, name_ar, legacy, color, sort),
        )
    return {"departments": len(DEPARTMENTS)}


# ─── Staff ───

# name, legacy role, legacy department, department_key, role_key, floors,
# languages. Dubai-typical names for a Deira 3-star team (02 spec 9). The
# first language is the one a person reads their jobs in (05 spec 2.10):
# Maria reads Tagalog (fil), Priya Hindi.
STAFF = (
    (DECK_ATTENDANT, "Room Attendant", "Housekeeping", "housekeeping", "staff", ["1", "2", "3", "4"], ["fil", "en"]),
    ("Joy Dela Cruz", "Room Attendant", "Housekeeping", "housekeeping", "staff", ["1", "2"], ["en", "tl"]),
    ("Anita Gurung", "Room Attendant", "Housekeeping", "housekeeping", "staff", ["3", "4"], ["en", "ne", "hi"]),
    (DECK_SUPERVISOR, "Housekeeping Supervisor", "Housekeeping", "housekeeping", "supervisor", [], ["en", "ml", "hi"]),
    ("Grace Wanjiru", "Room Attendant", "Housekeeping", "housekeeping", "staff", ["7", "8"], ["en", "sw"]),
    ("Sunil Perera", "Night Housekeeping Supervisor", "Housekeeping", "housekeeping", "supervisor", [], ["en", "si"]),
    ("Arjun Menon", "In-Room Dining Supervisor", "Food & Beverage", "ird", "supervisor", [], ["en", "ml", "hi"]),
    ("Ahmed Farouk", "Order Taker", "Food & Beverage", "ird", "staff", [], ["en", "ar"]),
    ("Kiran Thapa", "Room Service Waiter", "Food & Beverage", "ird", "staff", [], ["en", "ne", "hi"]),
    ("Ramesh Pillai", "Commis Chef", "Food & Beverage", "ird", "staff", [], ["en", "ta"]),
    ("Imran Qureshi", "Chief Engineer", "Maintenance", "engineering", "supervisor", [], ["en", "ur", "hi"]),
    ("Rajesh Kumar", "Maintenance Technician", "Maintenance", "engineering", "staff", [], ["en", "hi"]),
    ("Bilal Hassan", "Maintenance Technician", "Maintenance", "engineering", "staff", [], ["en", "ur"]),
    (DECK_DUTY_MANAGER, "Duty Manager", "Front Desk", "front_office", "duty_manager", [], ["en", "ar"]),
    (DECK_SECOND_ATTENDANT, "Room Attendant", "Housekeeping", "housekeeping", "staff", ["5", "6", "7", "8"],
     ["hi", "en"]),
    ("Mohammed Rafiq", "Night Auditor", "Front Desk", "front_office", "staff", [], ["en", "ar", "ur"]),
    ("Hessa Al Ali", "Assistant Front Office Manager", "Front Desk", "front_office", "duty_manager", [], ["ar", "en"]),
    # The GM is on the escalation ladder (10 minutes) but takes no jobs: no
    # Service department, so routing never offers them a towel run.
    (DECK_GM, "General Manager", "Management", None, "gm", [], ["en"]),
)


def demo_name(name: str) -> str:
    return name


def _pin(rng: Any, used: set[str]) -> str:
    """Four digits, no repeats or runs, unique in the hotel."""
    while True:
        pin = f"{rng.randint(1000, 9999)}"
        if len(set(pin)) >= 3 and pin not in used and pin not in "0123456789" and pin not in "9876543210":
            used.add(pin)
            return pin


def seed_staff(ctx: Any) -> dict:
    rng = ctx.rng("core:staff")
    used: set[str] = {DECK_ATTENDANT_PIN}
    v2 = ctx.table_exists("svc_departments")  # Service's v2 columns ship with its migrations
    ids: dict[str, int] = {}
    pins: dict[str, str] = {}
    for name, role, dept, dkey, rkey, floors, langs in STAFF:
        full = demo_name(name)
        ctx.cur.execute(
            """
            INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, floors)
            VALUES (%s, %s, %s, %s, TRUE, FALSE, %s) RETURNING id
            """,
            (ctx.slug, full, role, dept, floors),
        )
        sid = ctx.cur.fetchone()["id"]
        if v2:
            ctx.cur.execute(
                "UPDATE staff_members SET department_key = %s, role_key = %s, languages = %s WHERE id = %s",
                (dkey, rkey, langs, sid),
            )
        pin = DECK_ATTENDANT_PIN if name == DECK_ATTENDANT else _pin(rng, used)
        devices.set_staff_pin(ctx.cur, ctx.slug, sid, pin, actor=ACTOR)
        ids[name] = sid
        pins[full] = pin
    ctx.ids["staff"] = ids
    ctx.ids["staff_departments"] = {name: dkey for name, _r, _d, dkey, *_rest in STAFF}
    ctx.credentials["staff_pins"] = pins
    return {"staff": len(ids)}


# ─── Console users ───

# role, email local part, display name, departments. The duty manager is a
# department head over every department (empty list = all): the deck's
# approver of recovery gestures and the login /try hands reviewers.
USERS = (
    ("owner", "owner", "Khalid Al Mansoori", []),
    ("gm", "gm", DECK_GM, []),
    ("finance", "finance", "Vikram Iyer", []),
    ("revenue", "revenue", "Elena Petrova", []),
    ("front_desk", "frontdesk", "Aisha Rahman", ["front_office"]),
    (DUTY_MANAGER_ROLE, DUTY_MANAGER_LOCAL, DECK_DUTY_MANAGER, []),
)
# Staff rows that are the same person as a console login (staff_members.user_id).
STAFF_USER_LINKS = ((DECK_DUTY_MANAGER, DUTY_MANAGER_ROLE), (DECK_GM, "gm"))


def demo_email(local: str, slug: str) -> str:
    """example.com is reserved (RFC 2606): a magic link or invite sent to a
    demo user can never reach a real mailbox."""
    return f"{local}+{slug}@example.com"


def seed_users(ctx: Any) -> dict:
    """Users are global rows (no hotel_slug), so they are upserted by email
    and their password reset on every run; memberships were wiped with the
    hotel and are re-created. Passwords are random per run unless the CLI
    passes one (--password or CLEFS_DEMO_PASSWORD): a demo database that
    ends up reachable must not carry a password printed in the repo."""
    fixed = ctx.credentials.get("password")
    if fixed:
        passwords.validate_password(fixed)
    out: dict[str, dict] = {}
    ids: dict[str, int] = {}
    for role, local, name, depts in USERS:
        email = demo_email(local, ctx.slug)
        pw = fixed or secrets.token_urlsafe(9)
        ctx.cur.execute(
            """
            INSERT INTO users (email, name, password_hash, locale) VALUES (%s, %s, %s, 'en')
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, password_hash = EXCLUDED.password_hash,
                is_active = TRUE
            RETURNING id
            """,
            (email, demo_name(name), passwords.hash_password(pw)),
        )
        uid = ctx.cur.fetchone()["id"]
        ctx.cur.execute(
            """
            INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors, is_active, accepted_at)
            VALUES (%s, %s, %s, %s, '{}', TRUE, %s)
            ON CONFLICT (user_id, hotel_slug) DO UPDATE SET role = EXCLUDED.role, departments = EXCLUDED.departments,
                is_active = TRUE, updated_at = NOW()
            """,
            (uid, ctx.slug, role, depts, ctx.now),
        )
        ids[role] = uid
        out[role] = {"email": email, "password": pw, "name": demo_name(name)}
    staff = ctx.ids.get("staff") or {}
    for person, role in STAFF_USER_LINKS:
        if person in staff and role in ids:
            ctx.cur.execute("UPDATE staff_members SET user_id = %s WHERE id = %s AND hotel_slug = %s",
                            (ids[role], staff[person], ctx.slug))
    ctx.ids["users"] = ids
    ctx.credentials["users"] = out
    return {"users": len(ids)}


# ─── Device ───


def seed_device(ctx: Any) -> dict:
    """One paired front-desk tablet so the Devices page and the roster have
    something real. Its token is never printed (nobody holds it); the CLI
    mints a fresh pairing code for the presenter's own browser instead."""
    code = devices.create_pairing_code(
        ctx.cur, ctx.slug, name="Front Desk Tablet", kind="desk", floors=[],
        departments=["Front Desk", "Housekeeping"], actor=ACTOR,
    )["code"]
    _token, info = devices.pair(ctx.cur, code)
    ctx.ids["device_id"] = info.id
    return {"devices": 1}


# ─── Part entry ───


def seed_part(ctx: Any) -> dict:
    out: dict[str, Any] = {}
    for step in (seed_rooms, seed_tax, seed_channels, seed_departments, seed_staff, seed_users, seed_device):
        out.update(step(ctx))
    return out
