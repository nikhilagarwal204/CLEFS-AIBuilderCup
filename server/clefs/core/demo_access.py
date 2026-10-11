"""
Reviewer access to the demo hotel (05 spec section 5): the three one-tap
doors of the landing page, for hotels whose status is 'demo' and nothing
else. The prototype's hotel is Hotel Aozora Shinjuku, Tokyo
(aozora-shinjuku); the UAE demo hotel answers the same way while it is a demo.

    GET  /public/demo/{slug}             the cast, the keyed Room 204 link and the go-live gate in brief
    POST /public/demo/{slug}/staff-app   pairs this browser as Maria's phone and opens her shift on it
    POST /public/demo/{slug}/console     signs this browser in as the demo duty manager (Yuki Sato in Tokyo)

Why it is safe to leave open:
  - A hotel whose status is not 'demo' answers 404, worded like an unknown
    slug, so the routes neither work on nor reveal a real hotel, nor a demo
    hotel that was retired (python -m clefs.cli retire-demo).
  - Everything they create belongs to that one hotel: a personal device
    paired to it, a shift for one of its staff, and a console session for
    the demo duty manager, whose account is never a Clefs admin and whose
    only active memberships are demo hotels (sign-in is refused, 409, if
    anyone ever gave it a real hotel).
  - The console role is dept_head (the duty manager): it works the service
    desk and approves gestures, but cannot change hotel settings (status
    included), the team or the concierge's go-live switch.
  - Rate limits per caller IP and per hotel (clefs.ratelimit), and every
    grant is audited. Each staff-app tap pairs a new browser, so the oldest
    past MAX_TRY_DEVICES are revoked as new ones arrive.
  - The POSTs refuse a cross-site browser request (the app's origin check),
    so another site cannot sign a visitor's browser in.

The cast comes from the hotel's demo seed (clefs.seed.variants: the Tokyo
hotel's clefs.seed.demo_jp.core, the UAE hotel's clefs.seed.demo_uae.core),
which seeds these people, Maria's PIN and the duty manager's login; the
console door re-creates that login and its membership if a reseed has not
run yet.
"""

from __future__ import annotations

import importlib
import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Request, Response

from clefs import db, ratelimit
from clefs.audit import audit
from clefs.auth import devices, sessions
from clefs.core import clock, rooms
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.hotels import Hotel, find_hotel, module_enabled
from clefs.settings import get_settings

router = APIRouter(tags=["demo"])
log = logging.getLogger("clefs.demo_access")

CONSOLE_TTL = timedelta(hours=2)
SHIFT_LENGTH = timedelta(hours=12)
MAX_TRY_DEVICES = 30
DEVICE_PREFIX = "Try page"
# (attempts, window in seconds). Per caller IP, and per hotel for grants.
INFO_IP_LIMIT = (120, 60)
GRANT_IP_LIMIT = (20, 600)
GRANT_HOTEL_LIMIT = (400, 86400)

ANON = SimpleNamespace(kind="anonymous", user_id=None, device_id=None, label="try page")


# ─── Helpers ───


def _cast(hotel: Hotel) -> Any:
    """The deck cast of this demo hotel's seed (its variant's core module)."""
    from clefs.seed import variants

    return variants.cast_for(hotel.slug, hotel.country)


def _ip() -> str:
    return request_info().ip or "unknown"


def _limit(cur: Any, key: str, rule: tuple[int, int]) -> None:
    if not ratelimit.hit(cur, key, limit=rule[0], window_s=rule[1]):
        raise too_many("Too many demo requests. Wait a few minutes and try again.")


def _demo_hotel(cur: Any, slug: str, *, fresh: bool = False) -> Hotel:
    hotel = find_hotel(cur, slug, fresh=fresh)
    if hotel is None or hotel.status != "demo":
        raise ClefsError(404, f"Hotel '{slug}' not found", "hotel_not_found")
    return hotel


def _same_site(request: Request) -> None:
    from clefs.app import origin_allowed

    if not origin_allowed(request.headers, get_settings()):
        raise ClefsError(403, "Cross-site request blocked", "csrf_failed")


def _staff(cur: Any, slug: str, name: str) -> dict | None:
    cur.execute(
        "SELECT id, name, department, floors, languages FROM staff_members "
        "WHERE hotel_slug = %s AND name = %s AND is_active IS NOT FALSE ORDER BY id LIMIT 1",
        (slug, name),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _gate(cur: Any, slug: str) -> dict | None:
    """Trust's gate summary when the module is on (dynamic import: core
    never imports a module statically). None on any failure."""
    if not module_enabled(cur, slug, "trust"):
        return None
    cur.execute("SAVEPOINT demo_gate")
    try:
        out = importlib.import_module("clefs.modules.trust.api").gate_summary(cur, slug)
    except Exception as exc:  # noqa: BLE001 - the page shows the rest without it
        cur.execute("ROLLBACK TO SAVEPOINT demo_gate")
        log.info("demo gate summary unavailable for %s: %s", slug, exc)
        return None
    cur.execute("RELEASE SAVEPOINT demo_gate")
    return out


# ─── What /try shows ───


@router.get("/public/demo/{slug}")
def demo_info(slug: str) -> dict:
    """The deck cast and links for the reviewer page. The Room 204 link is
    keyed exactly like the room's printed QR card."""
    with db.db() as cur:
        _limit(cur, f"demo:info:ip:{_ip()}", INFO_IP_LIMIT)
        hotel = _demo_hotel(cur, slug)
        cast = _cast(hotel)
        room = rooms.get_room(cur, slug, cast.DECK_ROOM)
        maria = _staff(cur, slug, cast.DECK_ATTENDANT)
        gate = _gate(cur, slug)
    return {
        "hotel": {"slug": hotel.slug, "name": hotel.name, "city": hotel.city, "assistant_name": hotel.assistant_name,
                  "guest_languages": hotel.guest_languages,
                  "refreshed_at": hotel.updated_at.isoformat() if hotel.updated_at else None},
        "guest": {
            "room": cast.DECK_ROOM,
            "floor": room.get("floor"),
            "path": rooms.stay_path(slug, cast.DECK_ROOM, int(room.get("qr_key_version") or 1)),
        } if room else None,
        "staff": {
            "name": maria["name"],
            "first_name": maria["name"].split()[0],
            "department": maria.get("department"),
            "floors": list(maria.get("floors") or []),
            "languages": list(maria.get("languages") or []),
            "pin": cast.DECK_ATTENDANT_PIN,
        } if maria else None,
        "console": {"name": cast.DECK_DUTY_MANAGER, "role": "Duty manager"},
        "gate": gate,
    }


# ─── Maria's staff app ───


def _open_shift(cur: Any, device: devices.DeviceInfo, staff: dict, planned_end: Any) -> dict:
    """A PIN session for `staff` on `device`, opened by the demo door
    instead of a typed PIN, with the same side effects devices.start_shift
    has (the legacy online flag and shift log). Audited as such."""
    now = clock.now()
    cur.execute(
        """
        INSERT INTO device_sessions (device_id, hotel_slug, staff_member_id, started_at, planned_end_at, last_activity_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (device.id, device.hotel_slug, staff["id"], now, planned_end, now),
    )
    session_id = cur.fetchone()["id"]
    cur.execute("UPDATE staff_members SET is_online = TRUE WHERE id = %s AND is_online IS NOT TRUE RETURNING id",
                (staff["id"],))
    if cur.fetchone() is not None:
        cur.execute("INSERT INTO staff_shifts (hotel_slug, staff_name, check_in) VALUES (%s, %s, %s)",
                    (device.hotel_slug, staff["name"], now))
    audit(cur, ANON, "auth.device.shift_start", "device_session", session_id,
          meta={"device_id": device.id, "staff_member_id": staff["id"], "via": "demo_access"},
          hotel_slug=device.hotel_slug)
    return {"session_id": session_id, "staff": {"id": staff["id"], "name": staff["name"],
                                                "department": staff.get("department")},
            "planned_end_at": planned_end.isoformat()}


def _retire_old_devices(cur: Any, slug: str) -> int:
    cur.execute(
        "SELECT id FROM devices WHERE hotel_slug = %s AND kind = 'personal' AND name LIKE %s AND revoked_at IS NULL "
        "ORDER BY id DESC OFFSET %s",
        (slug, f"{DEVICE_PREFIX}:%", MAX_TRY_DEVICES),
    )
    old = [r["id"] for r in cur.fetchall()]
    for device_id in old:
        devices.revoke_device(cur, slug, device_id, actor=ANON)
    return len(old)


@router.post("/public/demo/{slug}/staff-app")
def demo_staff_app(slug: str, request: Request, response: Response) -> dict:
    """Pair this browser as Maria's phone (Housekeeping, her floors) and
    open her shift on it, so the staff app opens on her queue. Sets the
    clefs_device cookie, like POST /auth/devices/pair."""
    _same_site(request)
    with db.db() as cur:
        _limit(cur, f"demo:staff:ip:{_ip()}", GRANT_IP_LIMIT)
        cast = _cast(_demo_hotel(cur, slug, fresh=True))
        _limit(cur, f"demo:staff:hotel:{slug}", GRANT_HOTEL_LIMIT)
    with db.db() as cur:
        staff = _staff(cur, slug, cast.DECK_ATTENDANT)
        if staff is None:
            raise ClefsError(404, "The demo staff member is not set up yet", "demo_staff_not_found")
        first = staff["name"].split()[0]
        code = devices.create_pairing_code(
            cur, slug, name=f"{DEVICE_PREFIX}: {first}'s phone", kind="personal",
            floors=list(staff.get("floors") or []), departments=[staff.get("department") or "Housekeeping"], actor=ANON,
        )["code"]
        token, device = devices.pair(cur, code)
        session = _open_shift(cur, device, staff, clock.now() + SHIFT_LENGTH)
        retired = _retire_old_devices(cur, slug)
        audit(cur, ANON, "core.demo_access.staff_app", "device", device.id,
              meta={"staff_member_id": staff["id"], "retired_devices": retired}, hotel_slug=slug)
    sessions.set_device_cookie(response, token)
    return {"hotel_slug": slug, "device": device.json(), "session": session, "path": f"/device/{slug}",
            "pin": cast.DECK_ATTENDANT_PIN}


# ─── The manager console ───


def _duty_manager(cur: Any, slug: str, cast: Any) -> dict:
    """The demo duty manager's account (dept_head at this hotel only; Yuki
    Sato at the Tokyo hotel), created on first use when no reseed has made
    it yet."""
    email = cast.demo_email(cast.DUTY_MANAGER_LOCAL, slug)
    cur.execute(
        """
        INSERT INTO users (email, name, locale) VALUES (%s, %s, 'en')
        ON CONFLICT (email) DO UPDATE SET is_active = TRUE, is_clefs_admin = FALSE
        RETURNING id, name, email
        """,
        (email, cast.DECK_DUTY_MANAGER),
    )
    user = dict(cur.fetchone())
    sessions.evict_user(user["id"])
    cur.execute(
        """SELECT m.hotel_slug FROM hotel_memberships m JOIN hotels h ON h.slug = m.hotel_slug
           WHERE m.user_id = %s AND m.is_active AND h.status <> 'demo'""",
        (user["id"],),
    )
    if cur.fetchone() is not None:
        log.warning("demo duty manager %s has a membership at a real hotel; console sign-in refused", user["id"])
        raise ClefsError(409, "The demo login also has access to a hotel that is not a demo, so it is switched off",
                         "demo_login_not_isolated")
    cur.execute(
        """
        INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors, is_active, accepted_at)
        VALUES (%s, %s, %s, '{}', '{}', TRUE, %s)
        ON CONFLICT (user_id, hotel_slug) DO UPDATE SET role = EXCLUDED.role, departments = '{}', floors = '{}',
            is_active = TRUE, accepted_at = COALESCE(hotel_memberships.accepted_at, EXCLUDED.accepted_at),
            updated_at = NOW()
        """,
        (user["id"], slug, cast.DUTY_MANAGER_ROLE, clock.now()),
    )
    return user


@router.post("/public/demo/{slug}/console")
def demo_console(slug: str, request: Request, response: Response) -> dict:
    """Sign this browser in as the demo hotel's duty manager for CONSOLE_TTL
    (sets the clefs_session cookie, like POST /auth/login) and land on the
    service desk."""
    _same_site(request)
    with db.db() as cur:
        _limit(cur, f"demo:console:ip:{_ip()}", GRANT_IP_LIMIT)
        cast = _cast(_demo_hotel(cur, slug, fresh=True))
        _limit(cur, f"demo:console:hotel:{slug}", GRANT_HOTEL_LIMIT)
    info = request_info()
    with db.db() as cur:
        user = _duty_manager(cur, slug, cast)
        token, session_id, expires_at = sessions.create_session(
            cur, user["id"], kind="web", scope={"demo_access": slug}, ttl=CONSOLE_TTL, ip=info.ip,
            user_agent=info.user_agent,
        )
        audit(cur, SimpleNamespace(kind="user", user_id=user["id"], label=user["name"]), "core.demo_access.console",
              "auth_session", session_id, meta={"ttl_min": int(CONSOLE_TTL.total_seconds() // 60)}, hotel_slug=slug)
    sessions.set_session_cookie(response, token, expires_at)
    return {"hotel_slug": slug, "path": f"/app/{slug}/service", "role": cast.DUTY_MANAGER_ROLE,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
            "expires_at": expires_at.isoformat()}
