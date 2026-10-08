"""
Shared devices: floor tablets, department phones, the front-desk console.

Lifecycle:
  1. a manager mints a pairing code for a device spec (name, kind, floors,
     departments): POST /hotels/{slug}/devices/pairing-codes
  2. the tablet posts the code once: POST /auth/devices/pair -> clefs_device
     cookie (opaque token, hash stored, revocable)
  3. staff start a shift on it with their PIN: POST /auth/devices/shift/start
     -> a device_sessions row; X-Clefs-Staff then names who is acting
  4. shift end records the handover note

Canon 5: the foundation owns devices, pairing, PIN verification and
device_sessions; Service owns shift templates, roster rules and handover.

PIN lockout: 5 wrong PINs lock that staff member on THAT device for 5 minutes
(someone fat-fingering on one tablet does not lock them out hotel-wide), and 15
consecutive failures across devices lock them everywhere for 15 minutes.
"""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from types import SimpleNamespace
from datetime import datetime, timedelta
from typing import Any

from clefs import db
from clefs.audit import audit
from clefs.auth import passwords
from clefs.auth.tokens import hash_token, new_pairing_code, new_token, normalize_pairing_code
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json

DEVICE_KINDS = ("floor", "department", "desk", "phone_console", "personal")
PAIRING_CODE_TTL = timedelta(minutes=15)
PIN_MAX_FAILURES = 5
PIN_LOCK = timedelta(minutes=5)
GLOBAL_PIN_MAX_FAILURES = 15
GLOBAL_PIN_LOCK = timedelta(minutes=15)
DEFAULT_SHIFT = timedelta(hours=8)
CACHE_TTL_S = 30.0


@dataclass(frozen=True)
class DeviceInfo:
    id: int
    hotel_slug: str
    name: str
    kind: str
    floors: tuple[str, ...]
    departments: tuple[str, ...]

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "floors": list(self.floors),
            "departments": list(self.departments),
        }


_cache: dict[bytes, tuple[float, DeviceInfo | None]] = {}
_cache_lock = threading.Lock()


# (device_id, staff_member_id) -> monotonic time before which a new
# last_activity_at write is skipped (same idea as devices.last_seen_at).
_activity: dict[tuple[int, int], float] = {}
ACTIVITY_TOUCH_S = 60.0


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _activity.clear()


def touch_activity(cur: Any, device_id: int, staff_member_id: int) -> None:
    """Record that a person acted on this device: device_sessions.last_activity_at
    of their open PIN session. Service's auto-logout idle rule reads it.
    Called by the device principal loader for a write (not a GET) that names
    an on-shift staff member (X-Clefs-Staff); throttled to one write a minute
    per (device, person) per process, so the hot path stays a cache hit."""
    now_mono = _time.monotonic()
    key = (device_id, staff_member_id)
    with _cache_lock:
        if _activity.get(key, 0.0) > now_mono:
            return
        if len(_activity) > 10_000:  # bounded: stale entries only cost one extra write
            _activity.clear()
        _activity[key] = now_mono + ACTIVITY_TOUCH_S
    cur.execute(
        "UPDATE device_sessions SET last_activity_at = %s WHERE device_id = %s AND staff_member_id = %s AND ended_at IS NULL",
        (clock.now(), device_id, staff_member_id),
    )


def _actor(device: DeviceInfo) -> SimpleNamespace:
    """Audit actor for things a device does on its own behalf."""
    return SimpleNamespace(kind="device", device_id=device.id, user_id=None, label=device.name)


def _info(row: dict) -> DeviceInfo:
    return DeviceInfo(
        id=row["id"],
        hotel_slug=row["hotel_slug"],
        name=row["name"],
        kind=row["kind"],
        floors=tuple(row["floors"] or ()),
        departments=tuple(row["departments"] or ()),
    )


def lookup_device(token: str | None) -> DeviceInfo | None:
    if not token:
        return None
    key = hash_token(token)
    now_mono = _time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[0] > now_mono:
        return hit[1]
    with db.db() as cur:
        cur.execute(
            "SELECT id, hotel_slug, name, kind, floors, departments, last_seen_at FROM devices "
            "WHERE token_hash = %s AND revoked_at IS NULL",
            (key,),
        )
        row = cur.fetchone()
        info = _info(row) if row else None
        if row and (row["last_seen_at"] is None or clock.now() - row["last_seen_at"] > timedelta(minutes=5)):
            cur.execute("UPDATE devices SET last_seen_at = %s WHERE id = %s", (clock.now(), row["id"]))
    with _cache_lock:
        _cache[key] = (now_mono + CACHE_TTL_S, info)
    return info


def active_staff_ids(cur: Any, device_id: int) -> tuple[int, ...]:
    """Never cached: a shift started on another API process must count at once."""
    cur.execute(
        "SELECT staff_member_id FROM device_sessions WHERE device_id = %s AND ended_at IS NULL ORDER BY started_at",
        (device_id,),
    )
    return tuple(r["staff_member_id"] for r in cur.fetchall())


# ─── Pairing ───


def _clean_list(values: list[str] | None) -> list[str]:
    return [v.strip() for v in (values or []) if v and v.strip()]


def create_pairing_code(
    cur: Any,
    hotel_slug: str,
    *,
    name: str,
    kind: str,
    floors: list[str] | None,
    departments: list[str] | None,
    actor: Any,
) -> dict[str, Any]:
    if kind not in DEVICE_KINDS:
        raise ClefsError(400, f"kind must be one of {', '.join(DEVICE_KINDS)}", "invalid_kind")
    if not name.strip():
        raise ClefsError(400, "name is required", "invalid_name")
    code = new_pairing_code()
    expires_at = clock.now() + PAIRING_CODE_TTL
    spec = {"name": name.strip(), "kind": kind, "floors": _clean_list(floors), "departments": _clean_list(departments)}
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    cur.execute(
        "INSERT INTO device_pairing_codes (code_hash, hotel_slug, spec, created_by, expires_at) VALUES (%s, %s, %s, %s, %s)",
        (hash_token(code), hotel_slug, Json(spec), user_id, expires_at),
    )
    audit(cur, actor, "auth.device.pairing_code", "device_pairing_code", None, after=spec, hotel_slug=hotel_slug)
    return {"code": code, "expires_at": expires_at, "pair_url": f"/device/{hotel_slug}?code={code}"}


def pair(cur: Any, code: str) -> tuple[str, DeviceInfo]:
    """Consume a pairing code. Returns (device token, device)."""
    normalized = normalize_pairing_code(code)
    cur.execute(
        "SELECT code_hash, hotel_slug, spec, created_by, expires_at, used_at FROM device_pairing_codes "
        "WHERE code_hash = %s FOR UPDATE",
        (hash_token(normalized),),
    )
    row = cur.fetchone()
    if row is None or row["used_at"] is not None or row["expires_at"] <= clock.now():
        raise ClefsError(400, "This pairing code is invalid or has expired", "invalid_pairing_code")
    spec = row["spec"] or {}
    token = new_token()
    cur.execute(
        """
        INSERT INTO devices (hotel_slug, name, kind, floors, departments, token_hash, paired_by, paired_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, hotel_slug, name, kind, floors, departments
        """,
        (
            row["hotel_slug"],
            spec.get("name") or "Device",
            spec.get("kind") or "floor",
            spec.get("floors") or [],
            spec.get("departments") or [],
            hash_token(token),
            row["created_by"],
            clock.now(),
            clock.now(),
        ),
    )
    device = _info(cur.fetchone())
    cur.execute(
        "UPDATE device_pairing_codes SET used_at = %s, device_id = %s WHERE code_hash = %s",
        (clock.now(), device.id, row["code_hash"]),
    )
    audit(cur, _actor(device), "auth.device.paired", "device", device.id, after=device.json(), hotel_slug=device.hotel_slug)
    return token, device


def list_devices(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        "SELECT id, name, kind, floors, departments, paired_at, last_seen_at, revoked_at FROM devices "
        "WHERE hotel_slug = %s ORDER BY revoked_at IS NOT NULL, name, id",
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def revoke_device(cur: Any, hotel_slug: str, device_id: int, *, actor: Any) -> None:
    cur.execute(
        "UPDATE devices SET revoked_at = %s WHERE id = %s AND hotel_slug = %s AND revoked_at IS NULL RETURNING id, name",
        (clock.now(), device_id, hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Device not found", "device_not_found")
    cur.execute(
        "SELECT id, staff_member_id FROM device_sessions WHERE device_id = %s AND ended_at IS NULL",
        (device_id,),
    )
    for s in cur.fetchall():
        _close_session(cur, hotel_slug, s["id"], s["staff_member_id"], reason="device_revoked", note=None)
    with _cache_lock:
        for k in [k for k, (_, v) in _cache.items() if v is not None and v.id == device_id]:
            _cache.pop(k, None)
    audit(cur, actor, "auth.device.revoked", "device", device_id, before={"name": row["name"]}, hotel_slug=hotel_slug)


# ─── Roster and PIN shifts ───


def _scope_sql(device: DeviceInfo) -> tuple[str, list[Any]]:
    """Staff a device may serve: its departments (empty = all) and floors
    (empty = all; staff with no floors work every floor)."""
    clauses = ["s.hotel_slug = %s", "s.is_active IS NOT FALSE"]
    params: list[Any] = [device.hotel_slug]
    if device.departments:
        clauses.append("lower(s.department) = ANY(%s)")
        params.append([d.lower() for d in device.departments])
    if device.floors:
        clauses.append("(cardinality(s.floors) = 0 OR s.floors && %s)")
        params.append(list(device.floors))
    return " AND ".join(clauses), params


def roster(cur: Any, device: DeviceInfo) -> list[dict]:
    where, params = _scope_sql(device)
    cur.execute(
        f"""
        SELECT s.id, s.name, s.department, s.role, (s.pin_hash IS NOT NULL) AS has_pin,
               EXISTS (SELECT 1 FROM device_sessions ds
                       WHERE ds.staff_member_id = s.id AND ds.device_id = %s AND ds.ended_at IS NULL) AS on_shift
        FROM staff_members s
        WHERE {where}
        ORDER BY s.department, s.name
        """,
        [device.id] + params,
    )
    return [dict(r) for r in cur.fetchall()]


@dataclass
class ShiftOutcome:
    ok: bool
    data: dict | None = None
    error: ClefsError | None = None


def start_shift(cur: Any, device: DeviceInfo, staff_member_id: int, pin: str) -> ShiftOutcome:
    """Verify the PIN and open (or return the already open) device session.

    Returns an outcome instead of raising for a wrong PIN, because the failure
    counter must COMMIT even though the request fails; the route raises after
    its db() block has committed.
    """
    now = clock.now()
    where, params = _scope_sql(device)
    cur.execute(
        f"SELECT s.id, s.name, s.department, s.pin_hash, s.pin_locked_until, s.pin_failed_count, s.is_online "
        f"FROM staff_members s WHERE s.id = %s AND {where} FOR UPDATE",
        [staff_member_id] + params,
    )
    staff = cur.fetchone()
    if staff is None:
        return ShiftOutcome(False, error=ClefsError(404, "Staff member not found on this device", "staff_not_found"))
    if staff["pin_locked_until"] and staff["pin_locked_until"] > now:
        return ShiftOutcome(False, error=ClefsError(423, "Too many wrong PINs. Try again later.", "pin_locked"))
    cur.execute(
        "SELECT failed_count, locked_until FROM device_pin_failures WHERE device_id = %s AND staff_member_id = %s FOR UPDATE",
        (device.id, staff_member_id),
    )
    fail = cur.fetchone()
    if fail and fail["locked_until"] and fail["locked_until"] > now:
        return ShiftOutcome(False, error=ClefsError(423, "Too many wrong PINs. Try again in a few minutes.", "pin_locked"))
    if not staff["pin_hash"]:
        return ShiftOutcome(False, error=ClefsError(409, "No PIN is set for this staff member", "pin_not_set"))

    if not passwords.verify_pin(pin, staff["pin_hash"]):
        count = (fail["failed_count"] if fail else 0) + 1
        locked_until = now + PIN_LOCK if count >= PIN_MAX_FAILURES else None
        cur.execute(
            """
            INSERT INTO device_pin_failures (device_id, staff_member_id, failed_count, locked_until, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (device_id, staff_member_id) DO UPDATE SET
                failed_count = EXCLUDED.failed_count, locked_until = EXCLUDED.locked_until, updated_at = EXCLUDED.updated_at
            """,
            (device.id, staff_member_id, 0 if locked_until else count, locked_until, now),
        )
        total = (staff["pin_failed_count"] or 0) + 1
        global_lock = now + GLOBAL_PIN_LOCK if total >= GLOBAL_PIN_MAX_FAILURES else None
        cur.execute(
            "UPDATE staff_members SET pin_failed_count = %s, pin_locked_until = COALESCE(%s, pin_locked_until) WHERE id = %s",
            (0 if global_lock else total, global_lock, staff_member_id),
        )
        audit(
            cur,
            _actor(device),
            "auth.device.pin_failed",
            "staff_member",
            staff_member_id,
            meta={"device_id": device.id, "locked": bool(locked_until or global_lock)},
            hotel_slug=device.hotel_slug,
        )
        if locked_until or global_lock:
            return ShiftOutcome(False, error=ClefsError(423, "Too many wrong PINs. Try again in a few minutes.", "pin_locked"))
        return ShiftOutcome(
            False,
            error=ClefsError(401, f"Wrong PIN ({PIN_MAX_FAILURES - count} tries left)", "invalid_pin"),
        )

    cur.execute("DELETE FROM device_pin_failures WHERE device_id = %s AND staff_member_id = %s", (device.id, staff_member_id))
    cur.execute("UPDATE staff_members SET pin_failed_count = 0, pin_locked_until = NULL WHERE id = %s", (staff_member_id,))

    cur.execute(
        "SELECT id, planned_end_at FROM device_sessions WHERE device_id = %s AND staff_member_id = %s AND ended_at IS NULL",
        (device.id, staff_member_id),
    )
    existing = cur.fetchone()
    if existing:
        session_id, planned_end = existing["id"], existing["planned_end_at"]
    else:
        planned_end = now + DEFAULT_SHIFT
        cur.execute(
            """
            INSERT INTO device_sessions (device_id, hotel_slug, staff_member_id, started_at, planned_end_at, last_activity_at)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (device.id, device.hotel_slug, staff_member_id, now, planned_end, now),
        )
        session_id = cur.fetchone()["id"]
        # Keep the legacy auto-assigner and staff analytics in step: being on a
        # PIN shift means "online", exactly like the old online toggle.
        if not staff["is_online"]:
            cur.execute("UPDATE staff_members SET is_online = TRUE WHERE id = %s", (staff_member_id,))
            cur.execute(
                "INSERT INTO staff_shifts (hotel_slug, staff_name, check_in) VALUES (%s, %s, %s)",
                (device.hotel_slug, staff["name"], now),
            )
        audit(
            cur,
            _actor(device),
            "auth.device.shift_start",
            "device_session",
            session_id,
            meta={"device_id": device.id, "staff_member_id": staff_member_id},
            hotel_slug=device.hotel_slug,
        )
    return ShiftOutcome(
        True,
        data={
            "session_id": session_id,
            "staff": {"id": staff["id"], "name": staff["name"], "department": staff["department"]},
            "planned_end_at": planned_end,
        },
    )


def close_session(
    cur: Any, hotel_slug: str, session_id: int, staff_member_id: int, *, reason: str, note: str | None = None
) -> datetime:
    """End one PIN session (end_shift, device revocation, Service's
    auto-logout). The one place that keeps staff_members.is_online and the
    legacy staff_shifts in step with device_sessions. Returns the end time."""
    return _close_session(cur, hotel_slug, session_id, staff_member_id, reason=reason, note=note)


def _close_session(cur: Any, hotel_slug: str, session_id: int, staff_member_id: int, *, reason: str, note: str | None) -> datetime:
    now = clock.now()
    cur.execute(
        "UPDATE device_sessions SET ended_at = %s, end_reason = %s, handover_note = COALESCE(%s, handover_note) WHERE id = %s",
        (now, reason, note, session_id),
    )
    cur.execute(
        "SELECT 1 FROM device_sessions WHERE staff_member_id = %s AND ended_at IS NULL LIMIT 1",
        (staff_member_id,),
    )
    if cur.fetchone() is None:
        cur.execute(
            "UPDATE staff_members SET is_online = FALSE WHERE id = %s AND is_online RETURNING name",
            (staff_member_id,),
        )
        was_online = cur.fetchone()
        if was_online:
            cur.execute(
                """
                UPDATE staff_shifts SET check_out = %s, duration_minutes = EXTRACT(EPOCH FROM (%s - check_in)) / 60
                WHERE hotel_slug = %s AND staff_name = %s AND check_out IS NULL
                """,
                (now, now, hotel_slug, was_online["name"]),
            )
    return now


def end_shift(cur: Any, device: DeviceInfo, session_id: int, handover_note: str | None) -> dict:
    cur.execute(
        "SELECT id, staff_member_id FROM device_sessions WHERE id = %s AND device_id = %s AND ended_at IS NULL FOR UPDATE",
        (session_id, device.id),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "No open shift with that id on this device", "session_not_found")
    ended_at = _close_session(cur, device.hotel_slug, row["id"], row["staff_member_id"], reason="manual", note=handover_note)
    audit(
        cur,
        _actor(device),
        "auth.device.shift_end",
        "device_session",
        session_id,
        meta={"device_id": device.id, "staff_member_id": row["staff_member_id"], "handover": bool(handover_note)},
        hotel_slug=device.hotel_slug,
    )
    return {"ok": True, "session_id": session_id, "ended_at": ended_at}


def set_staff_pin(cur: Any, hotel_slug: str, staff_member_id: int, pin: str | None, *, actor: Any) -> None:
    """Set (or clear, pin=None) a staff member's PIN and clear any lockout."""
    pin_hash = None
    if pin is not None:
        try:
            pin_hash = passwords.hash_pin(pin)
        except ValueError as exc:
            raise ClefsError(400, str(exc), "invalid_pin_format") from exc
    cur.execute(
        "UPDATE staff_members SET pin_hash = %s, pin_failed_count = 0, pin_locked_until = NULL "
        "WHERE id = %s AND hotel_slug = %s RETURNING id",
        (pin_hash, staff_member_id, hotel_slug),
    )
    if cur.fetchone() is None:
        raise ClefsError(404, "Staff member not found", "staff_not_found")
    cur.execute("DELETE FROM device_pin_failures WHERE staff_member_id = %s", (staff_member_id,))
    audit(cur, actor, "auth.staff.pin_set" if pin else "auth.staff.pin_cleared", "staff_member", staff_member_id, hotel_slug=hotel_slug)
