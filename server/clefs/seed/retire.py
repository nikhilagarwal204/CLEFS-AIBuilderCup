"""
Retire a demo hotel: unreachable to the public, every row kept.

    python -m clefs.cli retire-demo --hotel demo-deira-crest
    python -m clefs.cli retire-demo --hotel demo-deira-crest --restore

Retiring changes state on existing rows only, in one transaction, so a
restore can undo it:

  - hotels.status demo -> archived. Every public door checks it: the demo
    access routes answer 404 (they serve status demo only), the guest
    page's context and the concierge's session mint answer 404 for an
    archived hotel, module_enabled() is false for every module (the room
    menu, the request tracker, the staff app's feeds), the scheduler skips
    the hotel and the seed refuses to rebuild it.
  - Console sign-in: every active membership at the hotel is switched off,
    and the demo logins (the seed's <role>+<slug>@example.com users) are
    deactivated with their sessions revoked, so neither a password nor a
    session already open reaches the console.
  - The staff app: every paired device is revoked (its open shifts end) and
    unused pairing codes expire now, so no browser can pair.
  - An audit row (core.demo.retired) records what was switched off.

Restoring (archived -> demo) switches those memberships and logins back on,
read from the latest core.demo.retired row; it refuses any hotel that row
does not name, so a hotel archived for real never becomes a demo. Devices
stay revoked: a reseed pairs Maria's phone again, and the landing page pairs
new browsers.

API processes cache hotels for 60 s and sessions and devices for 30 s, so a
retire reaches a running server within a minute.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from clefs.audit import audit
from clefs.auth import devices, sessions
from clefs.core import clock, hotels
from clefs.core.errors import ClefsError

ACTOR = SimpleNamespace(kind="system", user_id=None, device_id=None, label="retire-demo")
RETIRED, RESTORED = "core.demo.retired", "core.demo.restored"


def demo_login_pattern(slug: str) -> str:
    """The seed's demo logins (demo_uae.core.demo_email) as a LIKE pattern."""
    return "%+" + slug.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "@example.com"


def last_retire(cur: Any, slug: str) -> dict | None:
    cur.execute("SELECT id, at, meta FROM audit_log WHERE hotel_slug = %s AND action = %s ORDER BY id DESC LIMIT 1",
                (slug, RETIRED))
    row = cur.fetchone()
    return dict(row) if row else None


def retire(cur: Any, slug: str, *, actor: Any = ACTOR) -> dict:
    """Retire the demo hotel `slug` (see the module docstring). Returns what
    was switched off; {already: True} for a demo this command retired before.
    409 not_demo for any hotel that is not a demo."""
    hotel = hotels.find_hotel(cur, slug, fresh=True)
    if hotel is None:
        raise ClefsError(404, f"Hotel '{slug}' not found", "hotel_not_found")
    if hotel.status == "archived" and last_retire(cur, slug) is not None:
        return {"hotel": slug, "already": True}
    if hotel.status != "demo":
        raise ClefsError(409, f"{slug} is {hotel.status}, not a demo hotel; refusing to retire it", "not_demo")
    now = clock.now()
    hotels.update_hotel(cur, slug, {"status": "archived"})

    cur.execute("UPDATE hotel_memberships SET is_active = FALSE, updated_at = NOW() "
                "WHERE hotel_slug = %s AND is_active RETURNING id", (slug,))
    memberships = sorted(r["id"] for r in cur.fetchall())
    # A demo login with no other active membership is the demo's alone.
    cur.execute(
        """UPDATE users u SET is_active = FALSE WHERE u.email LIKE %s AND u.is_active
           AND NOT EXISTS (SELECT 1 FROM hotel_memberships m WHERE m.user_id = u.id AND m.is_active)
           RETURNING u.id""", (demo_login_pattern(slug),))
    users = sorted(r["id"] for r in cur.fetchall())
    revoked_sessions = sum(sessions.revoke_user_sessions(cur, uid) for uid in users)
    for uid in users:
        sessions.evict_user(uid)

    cur.execute("SELECT id FROM devices WHERE hotel_slug = %s AND revoked_at IS NULL ORDER BY id", (slug,))
    device_ids = [r["id"] for r in cur.fetchall()]
    for device_id in device_ids:
        devices.revoke_device(cur, slug, device_id, actor=actor)
    cur.execute("UPDATE device_pairing_codes SET expires_at = %s WHERE hotel_slug = %s AND used_at IS NULL "
                "AND expires_at > %s", (now, slug, now))
    codes = cur.rowcount

    out = {"hotel": slug, "already": False, "memberships": memberships, "users": users, "sessions": revoked_sessions,
           "devices": device_ids, "pairing_codes": codes}
    audit(cur, actor, RETIRED, "hotel", slug, before={"status": "demo"}, after={"status": "archived"},
          meta={k: v for k, v in out.items() if k not in ("hotel", "already")}, hotel_slug=slug)
    hotels.invalidate(slug)
    return out


def restore(cur: Any, slug: str, *, actor: Any = ACTOR) -> dict:
    """Undo retire(): status back to demo and the memberships and logins it
    switched off back on. 409 not_retired for a hotel no retire names."""
    hotel = hotels.find_hotel(cur, slug, fresh=True)
    if hotel is None:
        raise ClefsError(404, f"Hotel '{slug}' not found", "hotel_not_found")
    if hotel.status == "demo":
        return {"hotel": slug, "already": True}
    row = last_retire(cur, slug)
    if hotel.status != "archived" or row is None:
        raise ClefsError(409, f"{slug} was not retired by retire-demo (status {hotel.status}); refusing to restore it",
                         "not_retired")
    meta = row["meta"] or {}
    hotels.update_hotel(cur, slug, {"status": "demo"})
    cur.execute("UPDATE hotel_memberships SET is_active = TRUE, updated_at = NOW() "
                "WHERE hotel_slug = %s AND id = ANY(%s) AND NOT is_active RETURNING id",
                (slug, list(meta.get("memberships") or [])))
    memberships = sorted(r["id"] for r in cur.fetchall())
    cur.execute("UPDATE users SET is_active = TRUE WHERE id = ANY(%s) AND NOT is_active RETURNING id",
                (list(meta.get("users") or []),))
    users = sorted(r["id"] for r in cur.fetchall())
    for uid in users:
        sessions.evict_user(uid)
    out = {"hotel": slug, "already": False, "memberships": memberships, "users": users, "retired_at": row["at"]}
    audit(cur, actor, RESTORED, "hotel", slug, before={"status": "archived"}, after={"status": "demo"},
          meta={"memberships": memberships, "users": users, "retire_audit_id": row["id"]}, hotel_slug=slug)
    hotels.invalidate(slug)
    return out
