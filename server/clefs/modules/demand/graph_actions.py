"""
Graph actions from one event (02 spec 4.3). A relevant upcoming event fans
out into owner guidance, each an approvable action (actions.py):

- the rate recommendation (recommend.py, dr.recommend);
- a pre-arrival offer draft for guests already booked on the event nights
  (dr.pre_arrival_offer, a human task: nothing is sent from here);
- staffing guidance per event night: rooms_to_clean = departures +
  stayovers x stayover_ratio (0.8), attendants = ceil(rooms_to_clean /
  credits (14)); against the roster from service.api.on_duty a gap raises
  dr.staffing.gap. With no roster on file for the day the guidance is kept
  (roster_unknown) but no signal fires: an empty roster is not a gap;
- a KB update suggestion (metro route or traffic advice for the venue),
  queued through concierge.api.propose_kb_entry on approval, where the
  concierge's own approval still gates it.

Campaign drafts are campaigns.py. Everything is idempotent per (kind,
event, night) through dr_guidance.dedupe_key.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.signals import Evidence, RecommendedAction
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.demand import actions as dr_actions
from clefs.modules.demand import events, relevance as rel
from clefs.modules.demand import signals as sig

MIN_SCORE = 0.35          # events below this relevance get no guidance
HORIZON_DAYS = 45
MAX_STAFFING_NIGHTS = 10
STAFFING_CHECK_HOUR = time(10, 0)  # the roster is read at mid-morning, when rooms are cleaned
ACTIVE_STATUSES = ("CONFIRMED", "IN_HOUSE")

# Metro facts we are sure of; everything else gets generic traffic advice.
# The owner (and the concierge approval) checks every entry before it is live.
VENUE_METRO = {
    "DWTC": "World Trade Centre station on the Dubai Metro Red Line",
    "DEC": "Expo 2020 station at the end of the Dubai Metro Red Line (Route 2020)",
}


# ─── Shared: guidance rows ───


def _guidance(cur: Any, hotel_slug: str, *, kind: str, event_id: str | None, stay_date: date | None,
              dedupe_key: str, payload: dict) -> tuple[dict, bool]:
    """Upsert by (hotel, dedupe_key). Returns (row, created). A row the owner
    already decided keeps its status; only its payload refreshes."""
    row = clefs_db.fetch_one("SELECT * FROM dr_guidance WHERE hotel_slug = %s AND dedupe_key = %s",
                             (hotel_slug, dedupe_key), cur=cur)
    if row:
        clefs_db.execute("UPDATE dr_guidance SET payload = %s, updated_at = %s WHERE id = %s",
                         (Json(payload), clock.now(), row["id"]), cur=cur)
        return {**row, "payload": payload}, False
    row = clefs_db.fetch_one(
        "INSERT INTO dr_guidance (hotel_slug, event_id, kind, stay_date, dedupe_key, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (hotel_slug, event_id, kind, stay_date, dedupe_key, Json(payload)), cur=cur)
    return row, True


def _attach_action(cur: Any, row: dict, **kw: Any) -> None:
    act = dr_actions.create(cur, row["hotel_slug"], dedupe_key=f"dr.guidance:{row['id']}", **kw)
    if act.get("id"):
        clefs_db.execute("UPDATE dr_guidance SET action_id = %s WHERE id = %s", (act["id"], row["id"]), cur=cur)


def serialize(r: dict) -> dict:
    return {"id": r["id"], "event_id": str(r["event_id"]) if r["event_id"] else None, "kind": r["kind"],
            "stay_date": r["stay_date"].isoformat() if r["stay_date"] else None, "payload": r["payload"],
            "status": r["status"], "action_id": r["action_id"], "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat()}


def list_guidance(cur: Any, hotel_slug: str, *, kind: str | None = None, event_id: str | None = None,
                  limit: int = 200) -> list[dict]:
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_guidance WHERE hotel_slug = %s AND (%s::text IS NULL OR kind = %s) "
        "AND (%s::uuid IS NULL OR event_id = %s::uuid) ORDER BY stay_date NULLS LAST, id DESC LIMIT %s",
        (hotel_slug, kind, kind, event_id, event_id, limit), cur=cur)
    return [serialize(r) for r in rows]


def dismiss_guidance(cur: Any, hotel_slug: str, guidance_id: int) -> dict:
    row = clefs_db.fetch_one("SELECT * FROM dr_guidance WHERE id = %s AND hotel_slug = %s",
                             (guidance_id, hotel_slug), cur=cur)
    if row is None:
        raise ClefsError(404, "No such guidance", "guidance_not_found")
    clefs_db.execute("UPDATE dr_guidance SET status = 'DISMISSED', updated_at = %s WHERE id = %s",
                     (clock.now(), guidance_id), cur=cur)
    if row["action_id"]:
        from clefs.core import actions as core_actions

        dr_actions.safe_call(cur, core_actions.cancel, cur, row["action_id"], actor=core_actions.SYSTEM,
                             reason="dismissed")
    return serialize({**row, "status": "DISMISSED"})


# ─── Staffing guidance ───


def housekeeping_load(cur: Any, hotel_slug: str, d: date, *, stayover_ratio: float) -> dict:
    """Departures and stayovers on day d from the reservations on file
    (confirmed and in-house; rooms_count counts each room)."""
    r = clefs_db.fetch_one(
        "SELECT COALESCE(SUM(rooms_count) FILTER (WHERE COALESCE(actual_check_out, check_out) = %s), 0) AS departures, "
        "COALESCE(SUM(rooms_count) FILTER (WHERE check_in < %s AND COALESCE(actual_check_out, check_out) > %s), 0) "
        "AS stayovers FROM reservations WHERE hotel_slug = %s AND status = ANY(%s)",
        (d, d, d, hotel_slug, list(ACTIVE_STATUSES) + ["CHECKED_OUT"]), cur=cur)
    dep, stay = int(r["departures"]), int(r["stayovers"])
    return {"departures": dep, "stayovers": stay, "rooms_to_clean": round(dep + stay * stayover_ratio, 1)}


def attendants_needed(rooms_to_clean: float, credits: float) -> int:
    return math.ceil(rooms_to_clean / credits) if rooms_to_clean > 0 else 0


def rostered_attendants(cur: Any, hotel: Any, d: date) -> int | None:
    """Room attendants on shift at mid-morning (service.api.on_duty); None
    when Service is unavailable or nobody is rostered for that day yet."""
    from clefs.modules.service import api as service_api

    at = datetime.combine(d, STAFFING_CHECK_HOUR, tzinfo=hotel.tz)
    people = dr_actions.safe_call(cur, service_api.on_duty, cur, hotel.slug, role="attendant", at=at, default=None)
    if not people:
        return None
    return len({p.get("staff_member_id") or p.get("user_id") or p.get("name") for p in people})


def staffing_guidance(cur: Any, hotel: Any, d: date, *, event: dict | None = None) -> dict:
    s = rel.get_settings(cur, hotel.slug)
    load = housekeeping_load(cur, hotel.slug, d, stayover_ratio=s["stayover_ratio"])
    need = attendants_needed(load["rooms_to_clean"], s["credits_per_attendant"])
    have = rostered_attendants(cur, hotel, d)
    gap = None if have is None else max(0, need - have)
    payload = {**load, "stayover_ratio": s["stayover_ratio"], "credits_per_attendant": s["credits_per_attendant"],
               "attendants_needed": need, "attendants_rostered": have, "gap": gap, "roster_unknown": have is None,
               "event_title": event["title"] if event else None}
    eid = str(event["id"]) if event else None
    row, created = _guidance(cur, hotel.slug, kind="STAFFING", event_id=eid, stay_date=d,
                             dedupe_key=f"staffing:{d.isoformat()}", payload=payload)
    if gap:
        sig.raise_signal(
            cur, hotel.slug, kind=sig.STAFFING_GAP, dedupe_key=d.isoformat(),
            title=f"{d:%a %d %b}: {need} room attendants needed, {have} rostered",
            summary=f"{load['departures']} departures and {load['stayovers']} stayovers on the books"
                    + (f" around {event['title']}" if event else "") + ".",
            confidence=0.7, severity=3 if gap >= 2 else 2, due_at=datetime.combine(d, time(8, 0), tzinfo=hotel.tz),
            evidence=[Evidence(kind="calc", label="Rooms to clean",
                               value=f"{load['departures']} + {load['stayovers']} x {s['stayover_ratio']}"),
                      Evidence(kind="calc", label="Attendants", value=f"ceil({load['rooms_to_clean']} / "
                                                                      f"{s['credits_per_attendant']}) = {need}")],
            action=RecommendedAction(type=dr_actions.STAFFING, label=f"Add {gap} attendant shift(s)",
                                     params={"stay_date": d.isoformat(), "gap": gap}, owner_role="gm"),
            subject_type="dr_guidance", subject_id=str(row["id"]))
        if created or not row.get("action_id"):
            _attach_action(cur, row, type=dr_actions.STAFFING, title=f"Add {gap} room attendant(s) on {d:%a %d %b}",
                           params={"guidance_id": row["id"], "stay_date": d.isoformat(), "gap": gap,
                                   "event_id": eid}, owner_role="gm")
    return serialize(row) | {"payload": payload}


# ─── Pre-arrival offer draft ───


def booked_arrivals(cur: Any, hotel_slug: str, start: date, end: date) -> int:
    r = clefs_db.fetch_one(
        "SELECT count(*) AS n FROM reservations WHERE hotel_slug = %s AND status = 'CONFIRMED' "
        "AND check_in BETWEEN %s AND %s", (hotel_slug, start, end), cur=cur)
    return int(r["n"]) if r else 0


def pre_arrival_offer(cur: Any, hotel: Any, *, event: dict | None = None, start: date, end: date,
                      reason: str = "event") -> dict:
    """A drafted offer for guests already booked to arrive in [start, end]:
    around an event (early check-in, late check-out for show days, a transfer)
    or on a soft night (DI <= 35). The owner edits and sends it through the
    guest journey; nothing leaves from here."""
    n = booked_arrivals(cur, hotel.slug, start, end)
    if event:
        headline = f"Staying for {event['title']}?"
        body = (f"Make show days easier at {hotel.name}: request an early check-in, a late check-out or an airport "
                f"transfer before you arrive. Reply to this message and our team will confirm what is available.")
    else:
        headline = f"Before you arrive at {hotel.name}"
        body = ("Add breakfast, a room upgrade or a late check-out to your stay at a direct-guest price. "
                "Reply to this message and our team will confirm what is available.")
    payload = {"headline": headline, "body": body, "arrivals_from": start.isoformat(), "arrivals_to": end.isoformat(),
               "booked_arrivals": n, "reason": reason, "channel_hint": "guest journey (pre-arrival)",
               "event_title": event["title"] if event else None}
    eid = str(event["id"]) if event else None
    key = f"offer:{eid or 'soft'}:{start.isoformat()}"
    row, created = _guidance(cur, hotel.slug, kind="PRE_ARRIVAL_OFFER", event_id=eid, stay_date=start,
                             dedupe_key=key, payload=payload)
    if created and n > 0:
        _attach_action(cur, row, type=dr_actions.PRE_ARRIVAL_OFFER,
                       title=f"Pre-arrival offer to {n} booked guest(s), {start:%d %b} - {end:%d %b}",
                       params={"guidance_id": row["id"], "event_id": eid, "arrivals_from": start.isoformat(),
                               "arrivals_to": end.isoformat()}, owner_role="revenue")
    return serialize(row) | {"payload": payload}


# ─── KB update suggestion ───


def kb_text(event: dict, venue_rows: dict[str, dict]) -> tuple[str, str] | None:
    """(question, answer) for the concierge, or None without a venue."""
    codes = [c for c in event.get("venue_codes") or [] if c in venue_rows]
    if not codes:
        return None
    v = venue_rows[codes[0]]
    dates = f"{event['start_date']:%d %b} to {event['end_date']:%d %b %Y}"
    q = f"How do I get to {event['title']} at {v['name']}?"
    metro = VENUE_METRO.get(codes[0])
    a = (f"{event['title']} runs {dates} at {v['name']}. "
         + (f"The nearest metro stop is {metro}. " if metro else "")
         + "Roads around the venue are busier than usual on show days, so allow extra time by taxi or ride-hail, "
           "and ask the front desk to book a car in advance.")
    return q, a


def kb_suggestion(cur: Any, hotel: Any, event: dict) -> dict | None:
    venue_rows = {v["code"]: v for v in events.venues(cur)}
    qa = kb_text(event, venue_rows)
    if qa is None:
        return None
    eid = str(event["id"])
    payload = {"question_en": qa[0], "answer_en": qa[1], "event_title": event["title"],
               "note": "Check the route before approving: the concierge will say it to guests."}
    row, created = _guidance(cur, hotel.slug, kind="KB_UPDATE", event_id=eid, stay_date=event["start_date"],
                             dedupe_key=f"kb:{eid}", payload=payload)
    if created:
        _attach_action(cur, row, type=dr_actions.KB_UPDATE, title=f"Add travel advice for {event['title']}",
                       params={"guidance_id": row["id"], "event_id": eid, "question_en": qa[0], "answer_en": qa[1]},
                       owner_role="gm")
    return serialize(row) | {"payload": payload}


# ─── One event, and the daily pass ───


def for_event(cur: Any, hotel: Any, event: dict, relevance_row: dict | None = None) -> dict:
    """Every guidance item one event produces for this hotel."""
    nights = sorted(date.fromisoformat(k) for k, w in ((relevance_row or {}).get("nights") or {}).items() if w >= 0.6)
    if not nights:  # not scored yet: the category's own night mapping
        mapped = rel.nights_affected(event["category"], event["start_date"], event["end_date"],
                                     uncertainty_days=event.get("date_uncertainty_days") or 0,
                                     moon_dependent=bool(event.get("moon_dependent")),
                                     weekend=getattr(hotel, "weekend_days", None) or (6, 7))
        nights = sorted(d for d, w in mapped.items() if w >= 0.6) or [event["start_date"]]
    out = {"event_id": str(event["id"]), "staffing": [], "offer": None, "kb": None}
    # Housekeeping peaks the morning after each event night.
    for n in nights[:MAX_STAFFING_NIGHTS]:  # a month-long festival is not 30 staffing items
        out["staffing"].append(staffing_guidance(cur, hotel, n + timedelta(days=1), event=event)["payload"]["gap"])
    out["offer"] = pre_arrival_offer(cur, hotel, event=event, start=nights[0], end=nights[-1])["id"]
    kb = kb_suggestion(cur, hotel, event)
    out["kb"] = kb["id"] if kb else None
    return out


def run(cur: Any, hotel: Any, *, today: date | None = None, horizon_days: int = HORIZON_DAYS) -> dict:
    """dr.graph_actions: events scoring >= MIN_SCORE in the next 45 days."""
    today = today or local_date(hotel)
    rows = [r for r in rel.relevance_rows(cur, hotel.slug, today, today + timedelta(days=horizon_days))
            if float(r["score"]) >= MIN_SCORE and r["status"] not in ("CANCELLED", "POSTPONED")
            and r["start_date"] >= today]
    done = []
    for r in rows:
        ev = clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s", (r["event_id"],), cur=cur)
        if ev:
            done.append(for_event(cur, hotel, ev, r))
    return {"events": len(done)}


def _ref_finder(cur: Any, event_id: str) -> list[dict]:
    """events.references: guidance without an action still depends on the
    event (a drafted offer or KB text names its dates)."""
    rows = clefs_db.fetch_all("SELECT id, hotel_slug, status FROM dr_guidance WHERE event_id = %s "
                              "AND status IN ('PROPOSED', 'APPROVED') AND action_id IS NULL", (event_id,), cur=cur)
    return [{"hotel_slug": r["hotel_slug"], "ref_type": "guidance", "ref_id": r["id"],
             "approved": r["status"] == "APPROVED"} for r in rows]


events.register_ref_finder(_ref_finder)
