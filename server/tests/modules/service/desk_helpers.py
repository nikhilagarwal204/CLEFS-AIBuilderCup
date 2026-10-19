"""Helpers for the guest service desk tests (05 spec): staff on the roster,
a paired staff app, and Gemini answers by task through the fake_ai
FakeBackend (the safety classifier's own model call shares the backend, so
answers are matched by task, not by order)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import ai
from clefs.auth import devices
from clefs.core import clock

PIN = "4826"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 256
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256


def answer_by_task(fake_ai: Any, answers: dict[str, Any], *, n: int = 40) -> None:
    """From now on answer by task: `answers[task]` is a value the
    FakeBackend accepts, an exception, or a callable(req); a task with no
    answer fails like an outage (AIError), so the caller's fallback runs.
    Replaces whatever was queued before."""

    def respond(req: Any) -> Any:
        out = answers.get(req.task)
        if out is None:
            return ai.AIError(f"no answer queued for {req.task}")
        return out(req) if callable(out) else out

    fake_ai._queue.clear()
    fake_ai.push(*[respond] * n)


def tasks(fake_ai: Any) -> list[str]:
    return [c.task for c in fake_ai.calls if c.task != "service.safety"]


def staff(cur: Any, slug: str, name: str, department: str = "Housekeeping", *, languages: tuple[str, ...] = (),
          role_key: str | None = None, max_open: int = 4, pin: bool = False, phone: str | None = None) -> int:
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, languages, role_key,
               max_open, whatsapp_e164, whatsapp_opt_in)
           VALUES (%s, %s, 'Attendant', %s, TRUE, FALSE, %s, %s, %s, %s, %s) RETURNING id""",
        (slug, name, department, list(languages), role_key, max_open, phone, bool(phone)),
    )
    sid = cur.fetchone()["id"]
    if pin:
        devices.set_staff_pin(cur, slug, sid, PIN, actor=None)
    return sid


def roster(cur: Any, slug: str, staff_id: int, *, floors: tuple[str, ...] = (), role: str = "attendant",
           dept: str | None = "housekeeping", hours_before: int = 1, hours_after: int = 7) -> None:
    """A shift covering clock.now() (frozen or not)."""
    now = clock.now()
    cur.execute(
        """INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, end_at, floors,
               shift_role, department_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (slug, staff_id, now.date(), now - timedelta(hours=hours_before), now + timedelta(hours=hours_after),
         list(floors), role, dept),
    )


def login(dev: Any, staff_id: int) -> int:
    r = dev.post("/auth/devices/shift/start", json={"staff_member_id": staff_id, "pin": PIN})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def h(staff_id: int) -> dict:
    return {"X-Clefs-Staff": str(staff_id)}


def events(cur: Any, request_id: int, kind: str | None = None) -> list[dict]:
    sql = "SELECT kind, action, payload FROM svc_request_events WHERE request_id = %s"
    params: list[Any] = [request_id]
    if kind:
        sql += " AND kind = %s"
        params.append(kind)
    cur.execute(sql + " ORDER BY id", params)
    return [dict(r) for r in cur.fetchall()]
