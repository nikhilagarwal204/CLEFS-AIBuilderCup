"""
In-room phone AI public API (other modules import only this file).

Voice (Canon 11, vc_ tables, 0850-0899) is built by the Concierge lane. SIP
calls reuse concierge.api.build_surface_session(surface="phone"); the
functions here are read-only views for Trust's pilot scorecard and the
owner brief. Additive only: never change a published signature.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def call_stats(cur: Any, hotel_slug: str, *, start: datetime, end: datetime) -> dict:
    """Phone AI activity for calls started in [start, end):
    {calls, accepted, rejected, transferred, requests_created, emergencies,
    spoken_room, avg_duration_s (None without ended calls)}. All zero when
    the hotel has no lines (the default for every hotel)."""
    cur.execute(
        """
        SELECT count(*) AS calls,
               count(*) FILTER (WHERE accepted_at IS NOT NULL) AS accepted,
               count(*) FILTER (WHERE status = 'rejected') AS rejected,
               count(*) FILTER (WHERE transferred) AS transferred,
               COALESCE(sum(requests_created), 0) AS requests_created,
               COALESCE(sum(emergencies), 0) AS emergencies,
               count(*) FILTER (WHERE room_confirmed_by = 'spoken') AS spoken_room,
               avg(duration_s) FILTER (WHERE duration_s IS NOT NULL) AS avg_duration_s
        FROM vc_calls WHERE hotel_slug = %s AND started_at >= %s AND started_at < %s
        """,
        (hotel_slug, start, end),
    )
    row = dict(cur.fetchone() or {})
    out = {k: int(row.get(k) or 0) for k in ("calls", "accepted", "rejected", "transferred", "requests_created",
                                             "emergencies", "spoken_room")}
    avg = row.get("avg_duration_s")
    out["avg_duration_s"] = int(round(float(avg))) if avg is not None else None
    return out
