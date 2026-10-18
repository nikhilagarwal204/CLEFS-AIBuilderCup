"""
Service analytics v2 (03 spec 1.7): one grouped query per call, bucketed in
the hotel's timezone (never the server's, never UTC midnight).

- First response = acknowledged_at - timestamp (what the guest waited for a
  person to own it). Completion = resolved_at - timestamp (as the guest sees
  it); work time = resolved_at - acknowledged_at.
- Pre-v2 rows (intake_version IS NULL: legacy rows with no acknowledgement)
  are left out of every ack figure and counted separately as `pre_v2`.
- Late offline acks: when the ack's event arrived more than 120 s late
  (late_sync), the device's client_at is the ack time, but never earlier
  than assigned_at (a clock that runs early cannot make anyone look faster).
- The window is hotel-local dates [from, to], both included.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel

MAX_DAYS = 366

# ─── Group keys ───

GROUPS: dict[str, str] = {
    "staff": "COALESCE(r.completed_by_staff_id, r.assigned_staff_id)::text",
    "floor": "r.floor",
    "shift": "r.shift_key",
    "department": "COALESCE(r.department_key, r.category)",
    "room_type": "r.room_type",
    "language": "COALESCE(r.language, 'en')",
    "stay_stage": "r.stay_stage",
    "source": "r.source",
    "request_type": "COALESCE(r.request_type_key, r.type)",
    "severity": "r.severity",
    "day": "to_char((to_timestamp(r.\"timestamp\" / 1000.0) AT TIME ZONE %(tz)s)::date, 'YYYY-MM-DD')",
}
GroupBy = Literal["staff", "floor", "shift", "department", "room_type", "language", "stay_stage", "source",
                  "request_type", "severity", "day"]

# The ack instant analytics uses (see the module docstring). The LATERAL
# picks the request's ack event; its client_at counts only when late.
ACK_MS = """CASE WHEN ack.late_sync AND ack.client_at IS NOT NULL
                 THEN GREATEST((EXTRACT(EPOCH FROM ack.client_at) * 1000)::bigint, COALESCE(r.assigned_at, r."timestamp"))
                 ELSE r.acknowledged_at END"""
V2 = "r.intake_version IS NOT NULL"
ACKED = f'{V2} AND r.acknowledged_at IS NOT NULL AND r.acknowledged_at >= r."timestamp"'
DONE = "r.state = 'completed' AND r.resolved_at IS NOT NULL AND r.resolved_at >= r.\"timestamp\""


def _pct(expr: str, q: float, where: str) -> str:
    return (f"ROUND((percentile_cont({q}) WITHIN GROUP (ORDER BY ({expr}) / 60000.0) FILTER (WHERE {where}))::numeric, 1)")


def _rate(num: str, den: str) -> str:
    return f"ROUND(100.0 * COUNT(*) FILTER (WHERE {num}) / NULLIF(COUNT(*) FILTER (WHERE {den}), 0), 1)"


MEASURES = f"""
    COUNT(*) AS n,
    COUNT(*) FILTER (WHERE r.state = 'cancelled') AS n_cancelled,
    COUNT(*) FILTER (WHERE NOT ({V2})) AS pre_v2,
    COUNT(*) FILTER (WHERE {ACKED}) AS n_acked,
    {_pct(f'({ACK_MS}) - r."timestamp"', 0.5, ACKED)} AS ack_p50_min,
    {_pct(f'({ACK_MS}) - r."timestamp"', 0.9, ACKED)} AS ack_p90_min,
    COUNT(*) FILTER (WHERE {DONE}) AS n_completed,
    {_pct('r.resolved_at - r."timestamp"', 0.5, DONE)} AS resolve_p50_min,
    {_pct('r.resolved_at - r."timestamp"', 0.9, DONE)} AS resolve_p90_min,
    {_pct('r.resolved_at - r.acknowledged_at', 0.5, f'{DONE} AND {ACKED} AND r.resolved_at >= r.acknowledged_at')} AS work_p50_min,
    {_rate('r.sla_ack_breached', V2 + " AND r.state IS DISTINCT FROM 'cancelled'")} AS sla_ack_breach_rate,
    {_rate('r.sla_resolve_breached', V2 + " AND r.state IS DISTINCT FROM 'cancelled'")} AS sla_resolve_breach_rate,
    {_rate('r.reopen_count > 0', "r.state IS DISTINCT FROM 'cancelled'")} AS reopen_rate,
    COUNT(*) FILTER (WHERE r.guest_confirmation = 'yes') AS n_confirmed_yes,
    COUNT(*) FILTER (WHERE r.guest_confirmation = 'no') AS n_confirmed_no,
    {_rate("r.guest_confirmation = 'yes'", "r.guest_confirmation IN ('yes', 'no')")} AS guest_confirmed_rate
"""

# ─── Query ───

NUMERIC_KEYS = ("ack_p50_min", "ack_p90_min", "resolve_p50_min", "resolve_p90_min", "work_p50_min", "sla_ack_breach_rate",
                "sla_resolve_breach_rate", "reopen_rate", "guest_confirmed_rate")


def _clean(row: dict) -> dict:
    out = dict(row)
    for k in NUMERIC_KEYS:
        if out.get(k) is not None:
            out[k] = float(out[k])
    return out


def compute(cur: Any, hotel: Any, start: date, end: date, *, group_by: str | None = None) -> dict:
    """{from, to, timezone, group_by, totals, groups: [{key, label, ...}],
    days: [{day, n, ack_p50_min, ...}]} for requests filed in [start, end]."""
    if end < start:
        raise ClefsError(400, "`to` is before `from`", "bad_request")
    if (end - start).days >= MAX_DAYS:
        raise ClefsError(400, f"At most {MAX_DAYS} days at a time", "bad_request")
    if group_by is not None and group_by not in GROUPS:
        raise ClefsError(400, f"group_by must be one of {', '.join(GROUPS)}", "unknown_dimension")
    t0, t1 = htime.local_range(hotel, start, end)
    params = {"hotel": hotel.slug, "t0": htime.to_ms(t0), "t1": htime.to_ms(t1), "tz": hotel.timezone or "Asia/Dubai"}
    base = f"""
        FROM staff_requests r
        LEFT JOIN LATERAL (
            SELECT e.late_sync, e.client_at FROM svc_request_events e
            WHERE e.request_id = r.id AND e.action = 'ack' ORDER BY e.id LIMIT 1
        ) ack ON TRUE
        WHERE r.hotel_slug = %(hotel)s AND r."timestamp" >= %(t0)s AND r."timestamp" < %(t1)s"""
    cur.execute(f"SELECT {MEASURES} {base}", params)
    totals = _clean(cur.fetchone())
    groups: list[dict] = []
    if group_by:
        key = GROUPS[group_by]
        cur.execute(f"SELECT {key} AS key, {MEASURES} {base} GROUP BY 1 ORDER BY n DESC, key NULLS LAST LIMIT 500", params)
        groups = [_clean(r) for r in cur.fetchall()]
        _label(cur, hotel.slug, group_by, groups)
    cur.execute(f"SELECT {GROUPS['day']} AS day, {MEASURES} {base} GROUP BY 1 ORDER BY 1", params)
    days = [_clean(r) for r in cur.fetchall()]
    from clefs.modules.service import recovery

    totals["recovery_cost_minor"] = recovery.recovery_cost(cur, hotel.slug, start=start, end=end)
    totals["currency"] = hotel.currency or "AED"
    return {"from": start.isoformat(), "to": end.isoformat(), "timezone": params["tz"], "group_by": group_by,
            "totals": totals, "groups": groups, "days": days,
            "definitions": {"first_response": "acknowledged_at - created", "completion": "resolved_at - created",
                            "work": "resolved_at - acknowledged_at", "pre_v2": "legacy rows, left out of ack figures"}}


def _label(cur: Any, hotel_slug: str, group_by: str, groups: list[dict]) -> None:
    """Human labels: staff names for ids, department and type names."""
    from clefs.modules.service import config

    if group_by == "staff":
        ids = [int(g["key"]) for g in groups if g["key"] and str(g["key"]).isdigit()]
        names = {}
        if ids:
            cur.execute("SELECT id, name FROM staff_members WHERE hotel_slug = %s AND id = ANY(%s)", (hotel_slug, ids))
            names = {str(r["id"]): r["name"] for r in cur.fetchall()}
        for g in groups:
            g["label"] = names.get(str(g["key"]), "Unassigned" if g["key"] is None else str(g["key"]))
    elif group_by == "department":
        deps = config.departments(cur, hotel_slug)
        for g in groups:
            g["label"] = (deps.get(g["key"] or "") or {}).get("name") or g["key"] or "Unknown"
    elif group_by == "request_type":
        types = config.request_types(cur, hotel_slug)
        for g in groups:
            g["label"] = (types.get(g["key"] or "") or {}).get("label_en") or g["key"] or "Unknown"
    else:
        for g in groups:
            g["label"] = g["key"] if g["key"] is not None else "Unknown"


# ─── Route ───

router = APIRouter(prefix="/hotels/{slug}/service", tags=["service-analytics"])


@router.get("/analytics")
def analytics(
    slug: str,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    group_by: GroupBy | None = None,
    ctx: HotelCtx = Depends(require_perm("service.view")),
) -> dict:
    """Default window: the last 7 hotel-local days including today."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        end = to or htime.local_date(hotel)
        start = from_ or end - timedelta(days=6)
        return compute(cur, hotel, start, end, group_by=group_by)
