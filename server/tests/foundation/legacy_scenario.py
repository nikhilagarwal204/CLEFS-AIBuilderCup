"""
Golden legacy HTTP scenario.

One scripted walk through every legacy endpoint the pre-refactor `server/main.py`
exposed. It is run twice with the same code:

  1. against the OLD app (see golden/capture_legacy.py), which writes
     golden/legacy_contract.json;
  2. against the NEW app (tests/foundation/test_legacy_contract.py), which
     compares each step's status code, JSON shape and pinned values with that file.

Why shapes and pinned values rather than full bodies: ids, created_at and
epoch-ms values differ run to run, but key sets, value types and the business
outcome (priority, auto-assignee, category mapping, rate-limit wording) must not.
Anything the dashboards or the concierge read is pinned here.
"""

from __future__ import annotations

import time
from typing import Any, Callable

# A seeded legacy Indian hotel (HOTELS_DATA). Both apps create it on boot or
# migrate, and both treat it as open access (legacy_open_access after 0003).
LEGACY_SLUG = "hotel-nandan"


# ─── Shape extraction ───────────────────────────────────────


def shape(value: Any) -> Any:
    """Type tree of a JSON value. Lists are described by their first element,
    which is why the scenario orders data deterministically."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return ["list", shape(value[0]) if value else "empty"]
    if isinstance(value, dict):
        return {k: shape(value[k]) for k in sorted(value)}
    return type(value).__name__


def pick(value: Any, path: str) -> Any:
    """Tiny dotted-path getter: 'staff.0.name', 'hotel_kpis'."""
    cur = value
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# ─── Scenario ───────────────────────────────────────────────


class Recorder:
    """Runs requests and records {status, shape, values} per named step."""

    def __init__(self, client: Any):
        self.client = client
        self.steps: dict[str, dict[str, Any]] = {}

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        values: tuple[str, ...] = (),
        whole_body: bool = False,
    ) -> Any:
        resp = self.client.request(method, path, json=json, params=params)
        try:
            body = resp.json()
        except ValueError:
            body = {"__text__": resp.text}
        rec: dict[str, Any] = {
            "method": method,
            "path": path,
            "status": resp.status_code,
            "shape": shape(body),
        }
        if whole_body:
            rec["body"] = body
        if values:
            rec["values"] = {p: pick(body, p) for p in values}
        self.steps[name] = rec
        return body


def run_scenario(client: Any, slug: str = LEGACY_SLUG) -> dict[str, dict[str, Any]]:
    r = Recorder(client)
    h = f"/hotels/{slug}"
    base_ms = int(time.time() * 1000) - 60 * 60 * 1000  # an hour ago, strictly increasing below

    # ── root + hotel ──
    r.call("root", "GET", "/", whole_body=True)
    r.call(
        "hotel_get",
        "GET",
        h,
        values=("slug", "name", "star_rating", "rooms_count", "category", "city", "website"),
    )
    r.call("hotel_missing", "GET", "/hotels/does-not-exist", whole_body=True)

    # ── locations (concierge) ──
    r.call("locations_empty", "GET", f"{h}/locations", whole_body=True)
    loc = {"slug": "rooftop", "name": "Rooftop Bar", "type": "restaurant", "custom_instructions": "Mocktails only"}
    r.call("location_create", "POST", f"{h}/locations", json=loc, values=("slug", "name", "type", "custom_instructions", "hotel_slug"))
    r.call(
        "location_upsert",
        "POST",
        f"{h}/locations",
        json={**loc, "name": "Sky Lounge"},
        values=("slug", "name"),
    )
    r.call("locations_list", "GET", f"{h}/locations", values=("0.slug", "0.name"))
    r.call("location_get", "GET", f"{h}/locations/rooftop", values=("slug", "name"))
    r.call("location_missing", "GET", f"{h}/locations/missing", whole_body=True)
    r.call("location_delete", "DELETE", f"{h}/locations/rooftop", whole_body=True)

    # ── guests (roster) ──
    r.call("guests_empty", "GET", f"{h}/guests", whole_body=True)
    r.call("guests_seed", "POST", f"{h}/guests/seed", whole_body=True)
    r.call("guests_all", "GET", f"{h}/guests", whole_body=True)
    r.call(
        "guest_put",
        "PUT",
        f"{h}/guests/999",
        json={"data": {"guests": [{"name": "Test Guest", "type": "primary"}], "stayType": "solo"}},
        whole_body=True,
    )
    r.call("guest_delete", "DELETE", f"{h}/guests/999", whole_body=True)

    # ── staff ──
    r.call("staff_empty", "GET", f"{h}/staff", whole_body=True)
    r.call("staff_seed", "POST", f"{h}/staff/seed", values=("0.name", "0.department", "13.name"))
    person = {
        "name": "Test Person",
        "role": "Front Desk Agent",
        "department": "Front Desk",
        "phone_number": "9800000000",
    }
    created = r.call("staff_create", "POST", f"{h}/staff", json=person, values=("name", "is_active", "is_online", "phone_number"))
    staff_id = created["id"]
    r.call("staff_create_duplicate", "POST", f"{h}/staff", json=person)
    r.call("staff_get", "GET", f"{h}/staff/{staff_id}", values=("name", "department"))
    r.call("staff_get_missing", "GET", f"{h}/staff/999999", whole_body=True)
    r.call(
        "staff_patch",
        "PATCH",
        f"{h}/staff/{staff_id}",
        json={**person, "role": "Night Auditor", "is_online": False},
        values=("role", "is_online"),
    )
    r.call("staff_patch_missing", "PATCH", f"{h}/staff/999999", json=person, whole_body=True)
    r.call("staff_online", "PATCH", f"{h}/staff/{staff_id}/status", params={"is_online": "true"}, whole_body=True)
    r.call("staff_online_again", "PATCH", f"{h}/staff/{staff_id}/status", params={"is_online": "true"}, whole_body=True)
    r.call("staff_status_missing", "PATCH", f"{h}/staff/999999/status", params={"is_online": "true"}, whole_body=True)
    r.call("staff_offline", "PATCH", f"{h}/staff/{staff_id}/status", params={"is_online": "false"}, whole_body=True)
    r.call("staff_list", "GET", f"{h}/staff", values=("0.name", "0.department"))

    # ── requests ──
    r.call("requests_empty", "GET", f"{h}/requests", whole_body=True)
    towel = r.call(
        "request_create_towel",
        "POST",
        f"{h}/requests",
        json={
            "room": "502",
            "type": "Towel",
            "description": "Need 2 towels please",
            "category": "Housekeeping",
            "timestamp": base_ms,
        },
        values=("room", "type", "category", "priority", "status", "assigned_to"),
    )
    r.call(
        "request_create_spa",
        "POST",
        f"{h}/requests",
        json={
            "room": "305",
            "type": "Spa Booking",
            "description": "whenever you can, a massage slot",
            "category": "Concierge",
            "timestamp": base_ms + 1000,
        },
        values=("priority", "status", "assigned_to"),
    )
    r.call(
        "request_create_completed",
        "POST",
        f"{h}/requests",
        json={
            "room": "999",
            "type": "Other",
            "description": "no rush",
            "priority": "low",
            "status": "completed",
            "timestamp": base_ms + 2000,
        },
        values=("priority", "status", "assigned_to", "category"),
    )
    r.call(
        "request_complete",
        "PATCH",
        f"{h}/requests/{towel['id']}",
        json={"status": "completed", "resolved_at": towel["accepted_at"] + 5 * 60_000},
        whole_body=True,
    )
    r.call("request_patch_missing", "PATCH", f"{h}/requests/999999", json={"status": "completed"}, whole_body=True)
    r.call("request_create_invalid", "POST", f"{h}/requests", json={"room": "1"})

    # ── pending (guest-originated requests) ──
    r.call("pending_empty", "GET", f"{h}/pending", whole_body=True)
    r.call(
        "pending_ird",
        "POST",
        f"{h}/pending",
        json={
            "data": {
                "room": "701",
                "type": "In-Room Dining",
                "description": "Club sandwich",
                "category": "Clefs Request",
                "priority": "medium",
                "timestamp": base_ms + 3000,
            }
        },
        whole_body=True,
    )
    for i in range(5):
        r.call(
            f"pending_water_{i}",
            "POST",
            f"{h}/pending",
            json={
                "data": {
                    "room": "410",
                    "type": "Water",
                    "description": "extra water bottles",
                    "timestamp": base_ms + 4000 + i,
                }
            },
            whole_body=True,
        )
    r.call(
        "pending_rate_limited",
        "POST",
        f"{h}/pending",
        json={"data": {"room": "410", "type": "Water", "description": "more water"}},
        whole_body=True,
    )
    r.call("pending_clear", "DELETE", f"{h}/pending", whole_body=True)

    r.call(
        "requests_list",
        "GET",
        f"{h}/requests",
        values=(
            "0.room",
            "0.type",
            "0.status",
            "5.room",
            "5.category",
            "5.assigned_to",
            "5.priority",
            "7.room",
            "7.status",
            "8.room",
            "8.status",
        ),
    )
    listed = client.get(f"{h}/requests").json()
    r.steps["requests_list"]["count"] = len(listed)

    # ── analytics ──
    r.call(
        "staff_analytics",
        "GET",
        f"{h}/staff-analytics",
        values=(
            "hotel_kpis",
            "staff.0.name",
            "staff.0.completed_today",
            "staff.0.total_completed",
            "staff.0.avg_resolution_mins",
            "staff.0.task_type_breakdown",
            "staff.0.activity_log.0.status",
            "staff.0.activity_log.0.resolution_mins",
        ),
    )

    r.call("request_delete", "DELETE", f"{h}/requests/{towel['id']}", whole_body=True)
    r.call("staff_delete", "DELETE", f"{h}/staff/{staff_id}", whole_body=True)

    # ── conversation logs (concierge) ──
    r.call(
        "conv_log_post",
        "POST",
        f"{h}/conversation-logs",
        json={
            "session_id": "sess-1",
            "room_number": "502",
            "role": "user",
            "message": "Hello",
            "timestamp": base_ms,
        },
        whole_body=True,
    )
    r.call(
        "conv_log_post_2",
        "POST",
        f"{h}/conversation-logs",
        json={
            "session_id": "sess-1",
            "room_number": "502",
            "role": "assistant",
            "message": "Hi, how can I help?",
            "timestamp": base_ms + 10,
        },
        whole_body=True,
    )
    r.call(
        "conv_logs_by_room",
        "GET",
        f"{h}/conversation-logs",
        params={"room_number": "502"},
        values=("0.role", "0.message", "1.role", "0.session_id", "0.room_number"),
    )
    r.call("conv_logs_by_session", "GET", f"{h}/conversation-logs", params={"session_id": "sess-1"}, values=("1.message",))
    r.call("conv_logs_none", "GET", f"{h}/conversation-logs", params={"session_id": "nope"}, whole_body=True)
    r.call("conv_log_invalid", "POST", f"{h}/conversation-logs", json={"session_id": "x"})

    return r.steps


StepRunner = Callable[[Any, str], dict[str, dict[str, Any]]]
