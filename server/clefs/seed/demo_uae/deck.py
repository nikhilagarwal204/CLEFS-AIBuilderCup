"""
Demo part "deck": the live state the AI Builder Cup deck shows (05 spec
section 4), set up after every other part so it never changes the history
they replay.

- Maria's phone: a personal staff-app device for Housekeeping floors 1-4,
  paired, with her shift open on it until the end of her rostered fortnight
  (ops.DECK_ON_CALL). A personal device is never logged out for idling, so
  Maria is on her phone when the first guest asks for towels in Room 204.
- The shadow week is over: the hotel's service desk started in shadow mode
  with the history, more than 7 days before the anchor.
- Room 204 takes many guests at once: the Service setting guest_room_limit
  (guest-channel requests per room per 10 minutes) is at its maximum, 100,
  because several judges use the room during a pitch.
- The 22 standard safety and accuracy tests are queued through trust.api.
  The first minute tick after a reseed runs them, with Gemini as the judge,
  and the go-live gate shows the real result. No result is ever planted.
- The keyed Room 204 link and the /try page go to ctx.credentials, for the
  CLI to print next to the logins.

Module doors: trust.queue_eval_run (api.py). The rest are core services
(devices, rooms) and the Service table the sweep keeps (svc_sweep_state).

CHECKS / deck_checks: the cast is complete, on shift and paired (demo-check).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs.auth import devices
from clefs.core import rooms
from clefs.seed.demo_uae.core import (
    ACTOR, DECK_ATTENDANT, DECK_ATTENDANT_PIN, DECK_DUTY_MANAGER, DECK_GM, DECK_ROOM, DECK_SECOND_ATTENDANT,
    DECK_SUPERVISOR, DUTY_MANAGER_ROLE,
)
from clefs.seed.demo_uae.ops import DEMO_DAYS

PART_ORDER = 85

PHONE_NAME = "Maria's phone"
PHONE_FLOORS = ["1", "2", "3", "4"]
PHONE_DEPARTMENTS = ["Housekeeping"]
SHADOW_DAYS = 7
# Service's guest-channel limit per room per 10 minutes (ServiceSettings
# guest_room_limit, default 5, at most 100). During a pitch several judges
# ask from Room 204 at once, so the demo hotel runs at the maximum.
GUEST_ROOM_LIMIT = 100
# The 8 guest UI languages the hotel must offer (05 spec 2.14).
LANGUAGES = {"en", "ar", "ru", "hi", "ur", "zh", "de", "fr"}


# ─── Seeding ───


def fortnight_end(ctx: Any) -> Any:
    """The end of the cast's last rostered day (ops.seed_roster)."""
    return ctx.at(ctx.anchor + timedelta(days=DEMO_DAYS + 1), 0)


def pair_maria(ctx: Any) -> dict:
    """Maria's own staff-app phone, paired and with her shift open on it.
    The pairing goes through core devices like any phone; the token is
    dropped (nobody holds this phone), /try pairs each reviewer's browser
    as another of her devices."""
    sid = (ctx.ids.get("staff") or {}).get(DECK_ATTENDANT)
    if sid is None:
        return {"deck_phones": 0}
    code = devices.create_pairing_code(ctx.cur, ctx.slug, name=PHONE_NAME, kind="personal", floors=PHONE_FLOORS,
                                       departments=PHONE_DEPARTMENTS, actor=ACTOR)["code"]
    _token, info = devices.pair(ctx.cur, code)
    outcome = devices.start_shift(ctx.cur, info, sid, DECK_ATTENDANT_PIN)
    if not outcome.ok:
        raise outcome.error  # type: ignore[misc]  (the seed set this PIN a moment ago)
    ctx.cur.execute("UPDATE device_sessions SET planned_end_at = %s WHERE id = %s",
                    (fortnight_end(ctx), outcome.data["session_id"]))
    ctx.ids["deck_phone_id"] = info.id
    return {"deck_phones": 1}


def close_shadow_week(ctx: Any) -> dict:
    """The service desk's shadow period started with the history (the
    sweep's first run would have set it; the replayed sweeps start later at
    a small scale), so the shadow week is long done by the anchor."""
    if not ctx.table_exists("svc_sweep_state"):
        return {"shadow_days_done": 0}
    started = ctx.at(ctx.history_start, 9)
    ctx.cur.execute(
        """
        INSERT INTO svc_sweep_state (hotel_slug, shadow_started_at) VALUES (%s, %s)
        ON CONFLICT (hotel_slug) DO UPDATE
            SET shadow_started_at = LEAST(svc_sweep_state.shadow_started_at, EXCLUDED.shadow_started_at)
        """,
        (ctx.slug, started),
    )
    return {"shadow_days_done": SHADOW_DAYS}


def open_room_limit(ctx: Any) -> dict:
    """Raise the Service setting guest_room_limit to its maximum, kept with
    whatever else the hotel's Service settings say. Written after the
    history replays, so they keep the default limit they were planted with."""
    from clefs.core.hotels import module_settings_row, set_module_settings

    current = (module_settings_row(ctx.cur, ctx.slug, "service") or {}).get("settings") or {}
    set_module_settings(ctx.cur, ctx.slug, "service", {**current, "guest_room_limit": GUEST_ROOM_LIMIT}, actor=ACTOR)
    return {"guest_room_limit": GUEST_ROOM_LIMIT}


def queue_tests(ctx: Any) -> dict:
    """Queue the 22 standard tests. Run by the first tick after the reseed,
    on the real clock, against the knowledge base and prompt just seeded."""
    api = ctx.module_api("trust")
    if api is None or not hasattr(api, "queue_eval_run"):
        return {"tests_queued": 0}
    run = api.queue_eval_run(ctx.cur, ctx.slug, suites=["standard"], note="Queued by the demo seed")
    return {"tests_queued": 1 if run else 0}


def demo_links(ctx: Any) -> dict:
    """The keyed Room 204 link (what its printed QR card opens) and the
    reviewer page, for the CLI to print."""
    from clefs.settings import get_settings

    room = rooms.get_room(ctx.cur, ctx.slug, DECK_ROOM)
    if room is None:
        return {}
    base = get_settings().web_base_url.rstrip("/")
    links = {
        f"room_{DECK_ROOM}": base + rooms.stay_path(ctx.slug, DECK_ROOM, int(room.get("qr_key_version") or 1)),
        "try": f"{base}/try",
    }
    ctx.credentials["demo_links"] = links
    return links


def seed_part(ctx: Any) -> dict:
    out: dict[str, Any] = {}
    out.update(pair_maria(ctx))
    out.update(close_shadow_week(ctx))
    out.update(open_room_limit(ctx))
    out.update(queue_tests(ctx))
    demo_links(ctx)
    return out


# ─── Checks (demo-check) ───


def _one(cur: Any, sql: str, params: tuple) -> dict:
    cur.execute(sql, params)
    return dict(cur.fetchone() or {})


def _staff(cur: Any, slug: str, name: str) -> dict:
    cur.execute(
        "SELECT id, role, department, department_key, role_key, floors, languages, user_id FROM staff_members "
        "WHERE hotel_slug = %s AND name = %s AND is_active",
        (slug, name),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _covered_days(cur: Any, slug: str, staff_id: int | None) -> float:
    """Days of unbroken round-the-clock roster from the first 24-hour shift."""
    if staff_id is None:
        return 0.0
    cur.execute(
        "SELECT start_at, end_at FROM svc_shift_assignments WHERE hotel_slug = %s AND staff_member_id = %s "
        "AND status = 'planned' AND end_at - start_at >= interval '23 hours' ORDER BY start_at",
        (slug, staff_id),
    )
    rows = cur.fetchall()
    if not rows:
        return 0.0
    start, end = rows[0]["start_at"], rows[0]["end_at"]
    for r in rows[1:]:
        if r["start_at"] > end:
            break
        end = max(end, r["end_at"])
    return (end - start).total_seconds() / 86400


def deck_checks(cur: Any, slug: str) -> list[tuple[str, bool, str]]:
    """The deck cast as 05 spec section 4 lists it. Read-only; independent
    of the clock, so it reads the same at the anchor and a week later."""
    out: list[tuple[str, bool, str]] = []
    h = _one(cur, "SELECT assistant_name, guest_languages, status FROM hotels WHERE slug = %s", (slug,))
    langs = set(h.get("guest_languages") or [])
    out.append(("assistant Clefs in all 8 guest languages",
                h.get("assistant_name") == "Clefs" and LANGUAGES <= langs,
                f"assistant={h.get('assistant_name')} languages={len(langs)}"))
    room = _one(cur, "SELECT floor FROM hotel_rooms WHERE hotel_slug = %s AND room_number = %s", (slug, DECK_ROOM))
    out.append((f"room {DECK_ROOM} on floor 2", room.get("floor") == "2", f"floor={room.get('floor')}"))
    svc = _one(cur, "SELECT settings FROM hotel_module_settings WHERE hotel_slug = %s AND module = 'service'", (slug,))
    limit = (svc.get("settings") or {}).get("guest_room_limit")
    out.append((f"room {DECK_ROOM} open to many guests at once", limit == GUEST_ROOM_LIMIT,
                f"guest_room_limit={limit} per 10 minutes"))

    maria = _staff(cur, slug, DECK_ATTENDANT)
    out.append(("Maria: Housekeeping attendant, floors 1-4, reads Tagalog",
                maria.get("department_key") == "housekeeping" and maria.get("role_key") == "staff"
                and list(maria.get("floors") or []) == PHONE_FLOORS and (maria.get("languages") or [None])[0] == "fil",
                f"{maria.get('role')} floors={maria.get('floors')} languages={maria.get('languages')}"))
    days = _covered_days(cur, slug, maria.get("id"))
    out.append((f"Maria on shift round the clock for {DEMO_DAYS} days", days >= DEMO_DAYS, f"{days:.1f} days"))
    phone = _one(cur, """SELECT count(*) AS n FROM device_sessions ds JOIN devices d ON d.id = ds.device_id
                         WHERE ds.hotel_slug = %s AND ds.staff_member_id = %s AND ds.ended_at IS NULL
                           AND d.kind = 'personal' AND d.revoked_at IS NULL""", (slug, maria.get("id")))
    out.append(("Maria's phone paired, her shift open on it", (phone.get("n") or 0) >= 1, f"open={phone.get('n')}"))

    priya = _staff(cur, slug, DECK_SECOND_ATTENDANT)
    out.append(("Priya: Housekeeping attendant, floors 5-8, reads Hindi",
                priya.get("department_key") == "housekeeping" and priya.get("role_key") == "staff"
                and list(priya.get("floors") or []) == ["5", "6", "7", "8"]
                and (priya.get("languages") or [None])[0] == "hi",
                f"{priya.get('role')} floors={priya.get('floors')} languages={priya.get('languages')}"))

    ladder = {name: _staff(cur, slug, name).get("role_key") for name in (DECK_SUPERVISOR, DECK_DUTY_MANAGER, DECK_GM)}
    out.append(("supervisor, duty manager and GM on the ladder",
                list(ladder.values()) == ["supervisor", "duty_manager", "gm"],
                ", ".join(f"{k}={v}" for k, v in ladder.items())))
    dm = _one(cur, """SELECT count(*) AS n FROM hotel_memberships m JOIN users u ON u.id = m.user_id
                      WHERE m.hotel_slug = %s AND m.role = %s AND m.is_active AND u.name = %s""",
              (slug, DUTY_MANAGER_ROLE, DECK_DUTY_MANAGER))
    out.append(("duty manager console login", (dm.get("n") or 0) >= 1, f"{DUTY_MANAGER_ROLE} logins={dm.get('n')}"))

    shadow = _one(cur, "SELECT to_regclass('public.svc_sweep_state') IS NOT NULL AS ok", ())
    if shadow.get("ok"):
        sw = _one(cur, "SELECT shadow_started_at FROM svc_sweep_state WHERE hotel_slug = %s", (slug,))
        first = _one(cur, """SELECT min(start_at) AS t FROM svc_shift_assignments WHERE hotel_slug = %s
                             AND staff_member_id = %s AND end_at - start_at >= interval '23 hours'""",
                     (slug, maria.get("id")))
        started, live = sw.get("shadow_started_at"), first.get("t")
        done = bool(started and live and live - started >= timedelta(days=SHADOW_DAYS))
        out.append((f"{SHADOW_DAYS} days in shadow mode before the demo", done, f"shadow started {started}"))

    runs = _one(cur, "SELECT to_regclass('public.trs_eval_runs') IS NOT NULL AS ok", ())
    if runs.get("ok"):
        run = _one(cur, """SELECT status, total, passed FROM trs_eval_runs WHERE hotel_slug = %s
                           AND 'standard' = ANY(suites) AND status IN ('queued', 'running', 'completed')
                           ORDER BY id DESC LIMIT 1""", (slug,))
        detail = (f"{run.get('passed')}/{run.get('total')} passed" if run.get("status") == "completed"
                  else run.get("status") or "none")
        out.append(("22 standard tests queued or run", bool(run), detail))
    return out


CHECKS = [deck_checks]
