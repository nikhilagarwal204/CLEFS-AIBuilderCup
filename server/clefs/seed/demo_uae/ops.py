"""
Demo seed, operations part: Service requests, amenity issuance, reviews,
guest profiles and journeys, inquiries and upsell (02 spec 9 "Planted
cases", 03 spec, 04 spec 7.4, 00-PLAN "Demo dataset").

What it seeds and why each path:

  Service     ~3,000 history requests (scale 1.0) with realistic sources,
              languages and timings, SLA breaches clustered on Friday
              evenings, a roster, L1 incidents and recovery cases. The live
              episodes (the last Friday evening, three L1s, room 504's AC
              and its J5 repeat, last night's noise complaint, the anchor
              day, every in-room dining order) go through
              service.api.create_request with the clock frozen at their
              instant and the registered service.sla_sweep job every 5
              minutes, so routing, SLA flags, incidents and signals come from
              the real pipeline. The rest of the history is written as the
              completed rows that pipeline leaves (3,000 intake passes plus
              their sweeps would cost minutes). History and episodes are
              interleaved in time order: intake's rate limit, merge window
              and the sweep's "last 8 hours" count rows with no upper bound,
              so later history must not exist yet when an episode plays.
  Amenities   the issuance log enters as the housekeeping CSV export through
              clefs.core.ingest (kind ISSUANCE, the amenities commit handler),
              requests link through amenities.api.link_request, then the
              registered am.variance_rollup job runs at 03:30 on the anchor.
              Plants: dental kits floor 6 night shift ~3x for 10 nights
              (spike), slippers over-issued to one cart (spike), a
              shaving-kit logging gap, the single-use plastic flag; decoy:
              the Monday two-day closet fill (a doubled day, a normal week).
  Reviews     EN/AR/RU reviews on Google, Tripadvisor and a Booking CSV; the
              registered reviews.analyze job themes them, links the negative
              one that names a Friday-evening room to that request, raises
              the negative-review signals and drafts the replies.
  Guests      contacts and consent through guests.api (record_own_contact:
              registration card, console, the J1 page; opt_out), journey
              sends for recent stays, the J5 "AC is not cooling" reply whose
              ticket the anchor timeline files, J7 answers at 1-5 stars with
              the GM follow-up through service.api.create_followup.
  Inquiries   60 across channels through inquiries.api.create_inquiry, the
              tasks intake queues run at their run_at, then the 04 spec 7.4
              plants: 2 unanswered past SLA, a group quote awaiting approval,
              a lead leaked to Booking.com (inquiries.leak_match: AED 182.52)
              with three decoys, a verified direct booking from an N-Genius
              paid-webhook fixture (inquiries.verify: revenue 800.00,
              commission avoided 139.06 gross, 126.45 net), a Google
              duplicate delivery.
  Upsell      in-room dining sessions through upsell.api (quote, pairing
              accepted or not, confirm_order, record_upsell_event): the
              quote -> pairing -> order funnel, one order live tonight.

A section whose module is off (ctx.module_api returns None) or whose tables
are missing is skipped with a zero count, so the part runs on any build.

Plants go to ctx.plants as {detector, key, klass, module, dedupe_key,
subjects, note}: detector is the signal kind (or the row kind for a
non-signal plant), klass SIGNAL | ROW | DECOY. No database id is written, so
the manifest bytes depend only on the seed and the anchor.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterator

from clefs.core import clock

PART_ORDER = 60


# ─── Helpers ───


def ms(dt: datetime) -> int:
    """Epoch milliseconds: staff_requests keeps its legacy BIGINT instants."""
    return int(dt.timestamp() * 1000)


@contextmanager
def at(ctx: Any, instant: datetime) -> Iterator[datetime]:
    """Run a step as it would have run then: the clock is frozen at that
    instant and set back to the anchor evening after (the orchestrator's
    frozen "now"), so nothing downstream ever reads the wall clock."""
    clock.freeze(instant.astimezone(UTC))
    try:
        yield instant
    finally:
        clock.freeze(ctx.now.astimezone(UTC))


_LOADED: list[bool] = []


def _registry() -> Any:
    """clefs.jobs with every enabled module's jobs registered (a module's
    api.py alone does not import its jobs; the worker calls load_all too)."""
    from clefs import jobs

    if not _LOADED:
        jobs.load_all()
        _LOADED.append(True)
    return jobs


def _job_ctx(ctx: Any, name: str, payload: dict | None = None) -> Any:
    from clefs import jobs

    return jobs.JobCtx(cur=ctx.cur, job=name, hotel_slug=ctx.slug, scheduled_for=clock.now(), payload=payload or {})


def run_job(ctx: Any, name: str) -> Any:
    """A registered scheduled job's body, inside the seed transaction (the
    worker would commit after it returns; here the seed does). None when the
    job is not registered in this build (the module is off)."""
    jobs = _registry()
    j = next((j for j in jobs.registered_jobs() if j.name == name), None)
    return None if j is None else j.fn(_job_ctx(ctx, name))


def run_task(ctx: Any, name: str, payload: dict) -> Any:
    jobs = _registry()
    t = next((t for t in jobs.registered_tasks() if t.name == name), None)
    return None if t is None else t.fn(_job_ctx(ctx, name, payload), payload)


def plant(ctx: Any, detector: str, key: str, *, klass: str = "SIGNAL", module: str, dedupe_key: str | None = None,
          subjects: list | None = None, note: str = "", **extra: Any) -> dict:
    """One manifest entry. dedupe_key is the signal's own key when it has no
    database id in it; otherwise subjects name the planted rows by stable
    handles (room, pms_res_id, ref) and the test resolves them."""
    p = {"detector": detector, "key": key, "klass": klass, "module": module, "dedupe_key": dedupe_key,
         "subjects": subjects or [], "note": note, **extra}
    ctx.plants.append(p)
    return p


def stays_by_day(ctx: Any) -> dict[date, list[dict]]:
    """In-house reservations per night from ctx.ids (core_stays)."""
    out: dict[date, list[dict]] = {}
    for r in ctx.ids.get("reservations") or []:
        if r.get("status") in ("CANCELLED", "NO_SHOW") or not r.get("room_number"):
            continue
        d = r["check_in"]
        while d < r["check_out"]:
            out.setdefault(d, []).append(r)
            d += timedelta(days=1)
    return out


OPS_ID_BASE = 700000  # plant stays RC7xxxxx (core RC1xxxxx, money RC9xxxxx)


def free_room(ctx: Any, start: date, end: date, prefer: tuple[str, ...] = ()) -> str | None:
    """A room with no stay on any night in [start, end), preferring `prefer`,
    so a plant stay never double-books a core stay's room."""
    busy: set[str] = set()
    for r in ctx.ids.get("reservations") or []:
        if r.get("status") != "CANCELLED" and r.get("room_number") and r["check_in"] < end and r["check_out"] > start:
            busy.add(r["room_number"])
    for p in ctx.stats.get("ops_stays") or []:
        if p["room_number"] and p["check_in"] < end and p["check_out"] > start:
            busy.add(p["room_number"])
    rooms = sorted(ctx.ids.get("rooms") or {})
    for room in (*prefer, *rooms):
        if room in (ctx.ids.get("rooms") or {}) and room not in busy:
            return room
    return None


def ops_stay(ctx: Any, key: str, *, channel: str, check_in: date, nights: int, status: str, guest: tuple,
             room: str | None = None, rate: int = 320, contacts: list[dict] | None = None,
             ota_res_id: str | None = None, booked_at: datetime | None = None) -> dict:
    """One plant stay through stays.upsert_reservation (the single write
    path: nights, fees, chains, stay events, sealed contacts). guest is
    (name, country, language)."""
    from clefs.core import stays

    made = ctx.stats.setdefault("ops_stays", [])
    pms = f"RC{OPS_ID_BASE + len(made) + 1}"
    name, country, lang = guest
    co = check_in + timedelta(days=nights)
    info = (ctx.ids.get("rooms") or {}).get(room or "") or {}
    rtype = info.get("room_type_code") or "STD_KING"
    data: dict[str, Any] = {
        "pms_res_id": pms, "channel_code": channel, "ota_res_id": ota_res_id, "guest_name": name,
        "guest_country": country, "guest_language": lang,
        "booked_at": booked_at or ctx.at(check_in, 11) - timedelta(days=12), "check_in": check_in,
        "check_out": co, "rooms_count": 1, "room_type_code": rtype, "room_number": room, "rate_plan_code": "BAR",
        "status": status, "collect_model": "OTA_COLLECT" if channel == "EXPEDIA" else "HOTEL_COLLECT",
        "currency": "AED", "adults": 2,
    }
    if status in ("IN_HOUSE", "CHECKED_OUT"):
        data["checked_in_at"] = ctx.at(check_in, 15, 10)
    if status == "CHECKED_OUT":
        data["checked_out_at"] = ctx.at(co, 10, 40)
    nights_rows = [{"stay_date": check_in + timedelta(days=i), "rate_minor": rate * 100, "room_number": room,
                    "room_type_code": rtype} for i in range(nights)]
    res = stays.upsert_reservation(ctx.cur, ctx.slug, data, source_system="PMS", nights=nights_rows,
                                   contacts=contacts)
    stay = {"id": int(res["id"]), "key": key, "pms_res_id": pms, "room_number": room, "check_in": check_in,
            "check_out": co, "channel": channel, "guest_name": name, "language": lang, "country": country,
            "room_type": rtype, "status": status}
    made.append(stay)
    return stay


class ReplayAI:
    """The seed never calls a model. Intake translates non-English requests
    and review analysis classifies text through clefs.ai; this backend
    answers those calls from the text the seed itself wrote (the English
    original of a translated request, the planted themes of a review), the
    same shape a model returns. Any other task is refused like AI switched
    off, so a module's own fallback runs."""

    name = "demo-replay"

    def __init__(self) -> None:
        self.answers: dict[tuple[str, str], Any] = {}

    def add(self, task: str, key: str, answer: Any) -> None:
        self.answers[(task, key)] = answer

    def respond(self, req: Any) -> Any:
        from clefs import ai

        text = req.input if isinstance(req.input, str) else str(req.input)
        for (task, key), answer in self.answers.items():
            if task == req.task and key in text:
                out = answer(req) if callable(answer) else answer
                if isinstance(out, dict):
                    return ai.AIResponse(output_text=json.dumps(out, ensure_ascii=False), raw=out, tokens_in=0,
                                         tokens_out=0)
                return ai.AIResponse(output_text=str(out), tokens_in=0, tokens_out=0)
        raise ai.AIDisabled(f"demo replay has no answer for {req.task}")


@contextmanager
def replay_ai(backend: ReplayAI) -> Iterator[ReplayAI]:
    """Install the replay backend and keep clefs.ai's call log quiet: it logs
    on its own connection, and on a first seed the hotel row is not
    committed yet, so each log row would fail its foreign key noisily."""
    from clefs import ai

    prev = getattr(ai, "_backend_override", None)
    ai_log = logging.getLogger("clefs.ai")
    level = ai_log.level
    ai.set_backend(backend)
    ai_log.setLevel(logging.CRITICAL)
    try:
        yield backend
    finally:
        ai.set_backend(prev)
        ai_log.setLevel(level)


# ─── Service ───

# type key, weight, ack min, resolve min (03 spec 1.4 default SLA seeds; the
# ones the spec leaves out take the 5/45 fallback), English description.
REQUEST_MIX: tuple[tuple[str, int, int, int, str], ...] = (
    ("towels", 14, 3, 15, "Two extra bath towels please"),
    ("water", 12, 3, 10, "Can we get 4 bottles of water"),
    ("amenities", 11, 3, 15, "Need a dental kit and a shaving kit"),
    ("make_up_room", 8, 5, 45, "Please make up the room now"),
    ("pillows", 4, 5, 20, "One more pillow please"),
    ("linen", 3, 3, 15, "Please change the bed sheets"),
    ("extra_bed", 2, 5, 30, "We need an extra bed for our son"),
    ("ird", 9, 2, 30, "Club sandwich and a fresh orange juice"),
    ("breakfast", 3, 2, 25, "Breakfast in the room at 8 for two"),
    ("drinks", 3, 2, 15, "Two cold soft drinks please"),
    ("ac", 6, 5, 60, "The AC is not cooling"),
    ("plumbing", 3, 5, 30, "The shower drain is blocked"),
    ("electrical", 2, 5, 45, "The bedside lamp does not work"),
    ("maintenance", 2, 5, 45, "The wardrobe door is broken"),
    ("luggage", 5, 2, 10, "Please collect our luggage from the room"),
    ("concierge", 4, 5, 45, "Can you book a taxi to Dubai Mall at 6"),
    ("wake_up", 3, 5, 45, "Wake-up call at 5:30 tomorrow"),
    ("billing", 2, 5, 45, "Please send me the bill by email"),
    ("checkout", 3, 5, 45, "Can we get a late checkout until 2 pm"),
    ("front_desk", 3, 5, 45, "What time does the pool close"),
    ("complaint", 2, 5, 45, "The room next door is very noisy"),
    ("other", 1, 5, 45, "Please help with the TV channels"),
)
SLA = {k: (a, r) for k, _w, a, r, _d in REQUEST_MIX}
DESCRIPTIONS = {k: d for k, _w, _a, _r, d in REQUEST_MIX}

# What a guest actually typed, by language, for the types guests most often
# write in their own language (the Service console shows the original next
# to the English; history rows carry translated = TRUE).
ORIGINALS: dict[str, dict[str, str]] = {
    "ar": {"towels": "نحتاج منشفتين إضافيتين من فضلك", "water": "ممكن أربع قوارير ماء",
           "ac": "المكيف لا يبرد", "make_up_room": "من فضلك نظفوا الغرفة الآن",
           "ird": "ساندويتش كلوب وعصير برتقال طازج", "amenities": "نحتاج طقم أسنان وطقم حلاقة"},
    "ru": {"towels": "Пожалуйста, два дополнительных полотенца", "water": "Можно четыре бутылки воды",
           "ac": "Кондиционер не охлаждает", "make_up_room": "Пожалуйста, уберите номер сейчас",
           "ird": "Клаб-сэндвич и свежий апельсиновый сок", "amenities": "Нужен зубной набор и бритвенный набор"},
    "hi": {"towels": "कृपया दो अतिरिक्त तौलिये दें", "water": "चार बोतल पानी मिल सकता है",
           "ac": "एसी ठंडा नहीं कर रहा है", "make_up_room": "कृपया अभी कमरा साफ़ कर दें"},
    "zh": {"towels": "请再给两条浴巾", "water": "可以给四瓶水吗", "ac": "空调不制冷", "make_up_room": "请现在打扫房间"},
}

# source, weight. Guest channels first (WhatsApp leads in the UAE), then the
# staff channels that log calls and walk-ups.
SOURCES_MIX = (("whatsapp", 38), ("voice", 17), ("web", 15), ("phone_desk", 14), ("console", 7), ("device", 5),
               ("sip", 4))
GUEST_SOURCE_KEYS = {"whatsapp", "voice", "web", "sip"}
# Hourly weights 0-23: breakfast, the check-in wave and the evening peak.
HOUR_WEIGHTS = (1, 1, 1, 1, 1, 2, 4, 7, 8, 7, 5, 5, 5, 6, 8, 8, 7, 6, 8, 10, 10, 9, 6, 3)
DEPT_OF = {"towels": "housekeeping", "water": "housekeeping", "amenities": "housekeeping",
           "make_up_room": "housekeeping", "pillows": "housekeeping", "linen": "housekeeping",
           "extra_bed": "housekeeping", "ird": "ird", "breakfast": "ird", "drinks": "ird", "ac": "engineering",
           "plumbing": "engineering", "electrical": "engineering", "maintenance": "engineering",
           "luggage": "concierge", "concierge": "concierge", "wake_up": "front_office", "billing": "front_office",
           "checkout": "front_office", "front_desk": "front_office", "complaint": "front_office", "other": "general"}
LEGACY = {"housekeeping": "Housekeeping", "ird": "Food & Beverage", "engineering": "Maintenance",
          "front_office": "Front Desk", "concierge": "Concierge", "general": "General", "security": "Security"}
# Friday evening (18:00-23:59): the owner's planted pattern. The weekend
# check-in wave meets the thinnest evening roster, so about a third of
# those requests miss the ack or resolve target; elsewhere about 4% do.
FRIDAY_EVENING_BREACH = 0.36
BASE_BREACH = 0.04
REQUESTS_PER_OCCUPIED_ROOM = 0.52


def shift_of(hour: int) -> str:
    """The roster's shift templates: morning 07-15, evening 15-23, night 23-07."""
    return "morning" if 7 <= hour < 15 else "evening" if 15 <= hour < 23 else "night"


def is_friday_evening(t: datetime) -> bool:
    return t.weekday() == 4 and t.hour >= 18


REPEAT_ROOM = "504"  # the AC that fails twice in a week (and the J5 reply)
SWEEP_EVERY_MIN = 5


class Svc:
    """Service seeding state: staff by department and the in-house index."""

    def __init__(self, ctx: Any) -> None:
        self.ctx, self.cur, self.slug = ctx, ctx.cur, ctx.slug
        self.rng = ctx.rng("ops:service")
        self.nights = stays_by_day(ctx)
        self.rooms = ctx.ids.get("rooms") or {}
        staff, depts = ctx.ids.get("staff") or {}, ctx.ids.get("staff_departments") or {}
        self.by_dept: dict[str, list[tuple[str, int]]] = {}
        for name, sid in sorted(staff.items()):
            self.by_dept.setdefault(depts.get(name, "front_office"), []).append((name, sid))
        self.counts: dict[str, int] = {}

    def staff_for(self, dept: str) -> tuple[str, int] | None:
        pool = self.by_dept.get(dept) or self.by_dept.get("front_office") or []
        return self.rng.choice(pool) if pool else None

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def _pick(rng: Any, mix: tuple) -> Any:
    return rng.choices([m[0] for m in mix], weights=[m[1] for m in mix], k=1)[0]


def history_row(s: Svc, t: datetime, stay: dict, type_key: str, *, breach: bool) -> dict:
    """One completed request as intake and the device flow would have left
    it: acked, started and completed by a person of the right department,
    with the SLA flags the sweep would have set."""
    rng, room = s.rng, stay["room_number"]
    info = s.rooms.get(room) or {}
    dept = DEPT_OF[type_key]
    ack_min, res_min = SLA[type_key]
    source = _pick(rng, SOURCES_MIX)
    lang = stay.get("language") or "en"
    # Amenity asks stay in English (the item names are what the alias pass
    # links); other guest-channel requests keep the guest's own words.
    original = (ORIGINALS.get(lang, {}).get(type_key) if source in GUEST_SOURCE_KEYS and type_key != "amenities"
                else None)
    ack_s = int(ack_min * 60 * rng.uniform(0.25, 0.85))
    res_s = int(res_min * 60 * rng.uniform(0.35, 0.9))
    ack_breach = res_breach = False
    if breach:
        if rng.random() < 0.5:
            ack_s, ack_breach = int(ack_min * 60 * rng.uniform(1.4, 4.0)) + 60, True
        res_s, res_breach = max(res_s, ack_s) + int(res_min * 60 * rng.uniform(0.3, 1.2)) + 60, True
    res_s = max(res_s, ack_s + 60)
    t0 = ms(t)
    who = s.staff_for(dept)
    sev = "L2" if dept == "engineering" or type_key == "complaint" else "L3"
    return {
        "room": room, "type": type_key, "legacy_type": type_key,
        "description": rng.choice(AMENITY_ASKS)[0] if type_key == "amenities" else DESCRIPTIONS[type_key],
        "category": LEGACY[dept], "priority": "high" if sev == "L2" else "medium", "timestamp": t0,
        "assigned_to": f"{who[0]}" if who else None, "staff_id": who[1] if who else None,
        "acked": t0 + ack_s * 1000, "started": t0 + (ack_s + 30) * 1000, "resolved": t0 + res_s * 1000,
        "source": source, "language": lang if original else "en", "original_text": original,
        "translated": True if original else None, "floor": str(info.get("floor") or room[:-2]),
        "room_type": info.get("room_type_code") or stay.get("room_type"), "department_key": dept,
        "request_type_key": type_key, "severity": sev, "ack_due": t0 + ack_min * 60_000,
        "resolve_due": t0 + res_min * 60_000, "ack_breach": ack_breach, "res_breach": res_breach,
        "shift_key": shift_of(t.hour), "confirm": "yes" if rng.random() < 0.3 else None,
        "guest_ref": stay.get("pms_res_id"),
    }


def plan_history(s: Svc, start: date, end: date) -> list[tuple[datetime, dict, str, bool]]:
    """(created, stay, type, breach) for every history request in
    [start, end]: about 0.52 requests per occupied room night, Friday
    evenings carrying the breach cluster."""
    rng, out = s.rng, []
    fault_seen: dict[tuple, int] = {}
    d = start
    while d <= end:
        stays = s.nights.get(d) or []
        n = int(len(stays) * REQUESTS_PER_OCCUPIED_ROOM + rng.random())
        if d.weekday() == 4:
            n = int(n * 1.25)  # the weekend check-in wave
        for _ in range(n if stays else 0):
            hour = rng.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
            if d.weekday() == 4 and rng.random() < 0.25:
                hour = rng.choice((18, 19, 20, 21, 22))
            t = s.ctx.at(d, hour, rng.randrange(60))
            stay = rng.choice(stays)
            p = FRIDAY_EVENING_BREACH if is_friday_evening(t) else BASE_BREACH
            type_key = _pick(rng, REQUEST_MIX)
            if DEPT_OF[type_key] == "engineering" and (s.ctx.anchor - d).days < 15:
                # Inside the repeat-fault windows (7 days per room, 14 per room
                # type) random faults stay below the rule, so the brief shows
                # only the planted repeat (room 504's AC).
                k, kr = (stay.get("room_type"), type_key), (stay["room_number"], type_key)
                if type_key == "ac" or fault_seen.get(k, 0) >= 2 or kr in fault_seen:
                    type_key = "towels"
                else:
                    fault_seen[k] = fault_seen.get(k, 0) + 1
                    fault_seen[kr] = 1
            out.append((t, stay, type_key, rng.random() < p))
        d += timedelta(days=1)
    return sorted(out, key=lambda x: x[0])


# The legacy `type` column (what the pre-v2 dashboards and SSE clients read),
# as service.constants.REQUEST_TYPES maps it.
LEGACY_TYPE = {"towels": "Towel", "linen": "Clean Sheets", "amenities": "Cleaning", "water": "Water",
               "pillows": "Cleaning", "extra_bed": "Cleaning", "make_up_room": "Cleaning", "ird": "In-Room Dining",
               "breakfast": "In-Room Dining", "drinks": "Food", "luggage": "Luggage Assistance", "concierge": "Other",
               "ac": "Maintenance", "plumbing": "Water Supply", "electrical": "Maintenance",
               "maintenance": "Maintenance", "wake_up": "Wake-Up Call", "billing": "Billing", "checkout": "Checkout",
               "front_desk": "Front Desk", "complaint": "Direct Conversation", "other": "Other"}

_HISTORY_COLS = (
    "hotel_slug, room, type, description, category, priority, status, \"timestamp\", assigned_to, accepted_at, "
    "resolved_at, intake_version, state, version, source, language, original_text, translated, floor, room_type, "
    "stay_stage, department_key, request_type_key, severity, assigned_staff_id, assigned_at, acknowledged_at, "
    "started_at, ladder_started_at, ack_due_at, resolve_due_at, sla_policy_ref, sla_ack_breached, "
    "sla_resolve_breached, shift_key, completed_by_staff_id, resolution_code, guest_confirmation, "
    "guest_confirmed_at, guest_ref, flags"
)


def insert_history(s: Svc, rows: list[dict]) -> list[int]:
    """Write completed history rows plus their created / acknowledged /
    completed events in two statements (execute_values), returning ids in
    row order."""
    from psycopg2.extras import Json, execute_values

    if not rows:
        return []
    vals = [(
        s.slug, r["room"], LEGACY_TYPE[r["type"]], r["description"], r["category"], r["priority"], "completed",
        r["timestamp"], r["assigned_to"], r["acked"], r["resolved"], 2, "completed", 4, r["source"], r["language"],
        r["original_text"], r["translated"], r["floor"], r["room_type"], "in_house", r["department_key"],
        r["request_type_key"], r["severity"], r["staff_id"], r["acked"], r["acked"], r["started"], r["timestamp"],
        r["ack_due"], r["resolve_due"], f"global:request_type:{r['request_type_key']}", r["ack_breach"],
        r["res_breach"], r["shift_key"], r["staff_id"], "done", r["confirm"],
        r["resolved"] + 600_000 if r["confirm"] else None, r["guest_ref"], Json({"demo": True}),
    ) for r in rows]
    got = execute_values(s.cur, f"INSERT INTO staff_requests ({_HISTORY_COLS}) VALUES %s RETURNING id", vals,
                         page_size=1000, fetch=True)
    ids = [g["id"] for g in got]
    events = []
    for rid, r in zip(ids, rows):
        actor = r["assigned_to"] or "Front desk"
        for when, kind, frm, to in ((r["timestamp"], "created", None, "new"),
                                    (r["acked"], "transition", "assigned", "acknowledged"),
                                    (r["resolved"], "transition", "in_progress", "completed")):
            events.append((s.slug, rid, datetime.fromtimestamp(when / 1000, UTC), kind, frm, to,
                           "staff" if kind == "transition" else "guest", r["staff_id"] if frm else None,
                           actor if frm else None, r["floor"], r["department_key"]))
    execute_values(s.cur, "INSERT INTO svc_request_events (hotel_slug, request_id, at, kind, from_state, to_state, "
                          "actor_type, actor_staff_id, actor_label, floor, department_key) VALUES %s",
                   events, page_size=2000)
    return ids


# ─── Service: live timelines through intake and the sweep ───
# A timeline is a list of steps (instant, kind, spec). Requests are created
# through service.api.create_request with the clock frozen at their instant,
# the registered service.sla_sweep job runs every 5 minutes in between (as
# the worker would), and acknowledgements and completions land as the
# device flow would have written them. The sweep therefore sets the breach
# flags and raises the cluster signal itself.


def _transition(s: Svc, rid: int, t: datetime, to: str, who: tuple[str, int] | None) -> None:
    from psycopg2.extras import Json

    cur, now = s.cur, ms(t)
    cur.execute("SELECT state, floor, department_key FROM staff_requests WHERE id = %s", (rid,))
    row = cur.fetchone()
    if row is None or row["state"] in ("completed", "cancelled"):
        return
    sid, label = (who[1], f"{who[0]}") if who else (None, "Front desk")
    if to == "acknowledged":
        cur.execute(
            """UPDATE staff_requests SET state = 'acknowledged', status = 'progress', acknowledged_at = %s,
                   assigned_at = COALESCE(assigned_at, %s), accepted_at = COALESCE(accepted_at, %s),
                   assigned_staff_id = COALESCE(%s, assigned_staff_id), assigned_to = COALESCE(%s, assigned_to),
                   version = version + 1 WHERE id = %s""",
            (now, now, now, sid, label if who else None, rid),
        )
    else:
        cur.execute(
            """UPDATE staff_requests SET state = 'completed', status = 'completed', resolved_at = %s,
                   acknowledged_at = COALESCE(acknowledged_at, %s), started_at = COALESCE(started_at, %s),
                   completed_by_staff_id = COALESCE(%s, assigned_staff_id), resolution_code = 'done',
                   version = version + 1 WHERE id = %s""",
            (now, now, now, sid, rid),
        )
    cur.execute(
        """INSERT INTO svc_request_events (hotel_slug, request_id, at, kind, action, from_state, to_state,
               actor_type, actor_staff_id, actor_label, floor, department_key, payload)
           VALUES (%s, %s, %s, 'transition', %s, %s, %s, 'staff', %s, %s, %s, %s, %s)""",
        (s.slug, rid, t, "ack" if to == "acknowledged" else "complete", row["state"], to, sid, label,
         row["floor"], row["department_key"], Json({"demo": True})),
    )


def run_timeline(s: Svc, api: Any, steps: list[tuple[datetime, str, dict]], *, until: datetime) -> dict[str, dict]:
    """Play the steps in order with a sweep every 5 minutes. -> {key: row}."""
    made: dict[str, dict] = {}
    steps = sorted(steps, key=lambda x: (x[0], x[1] != "create"))
    if not steps:
        return made
    t = steps[0][0].replace(second=0, microsecond=0)
    i = 0
    while t <= until:
        while i < len(steps) and steps[i][0] <= t:
            when, kind, spec = steps[i]
            i += 1
            if kind == "create":
                with at(s.ctx, when):
                    made[spec["key"]] = api.create_request(
                        s.cur, s.slug, room=spec["room"], type=spec["type"], description=spec["text"],
                        source=spec.get("source", "whatsapp"), language=spec.get("language", "en"),
                        priority_hint=spec.get("priority_hint"), guest_ref=spec.get("guest_ref"),
                        idempotency_key=f"demo:{spec['key']}")
                s.bump("service_live")
                _on_time(steps, i, made[spec["key"]], spec["key"])
            elif spec["key"] in made:
                _transition(s, made[spec["key"]]["id"], when, kind, spec.get("who"))
        with at(s.ctx, t):
            run_job(s.ctx, "service.sla_sweep")
        t += timedelta(minutes=SWEEP_EVERY_MIN)
    return made


def _on_time(steps: list, i: int, row: dict, key: str) -> None:
    """Steps marked on_time land before the due times the service's own SLA
    policy gave the new row (the seed's table only approximates it), then
    the rest of the timeline is re-sorted."""
    ack_due, res_due = row.get("ack_due_at"), row.get("resolve_due_at")
    for k in range(i, len(steps)):
        when, kind, spec = steps[k]
        if spec.get("key") != key or not spec.get("on_time"):
            continue
        due = ack_due if kind == "acknowledged" else res_due
        if due:
            latest = datetime.fromtimestamp(int(due) / 1000 - (30 if kind == "acknowledged" else 90), UTC)
            steps[k] = (min(when, latest), kind, spec)
    steps[i:] = sorted(steps[i:], key=lambda x: (x[0], x[1] != "create"))


def last_friday(anchor: date) -> date:
    """The most recent Friday strictly before the anchor."""
    return anchor - timedelta(days=(anchor.weekday() - 4) % 7 or 7)


FRIDAY_TYPES = ("towels", "water", "ird", "amenities", "pillows", "drinks", "towels", "make_up_room")


def friday_steps(s: Svc, fri: date) -> list[tuple[datetime, str, dict]]:
    """The last Friday evening: eight requests between 18:10 and 20:30, the
    evening shift one attendant short. The first three are acknowledged
    only after 6-8 minutes (ack breaches; still before the ladder pages the
    GM at 10), the rest are acknowledged quickly and finished late."""
    stays = sorted(s.nights.get(fri) or [], key=lambda r: r["room_number"])
    rng, steps = s.rng, []
    if not stays:
        return steps
    for i, type_key in enumerate(FRIDAY_TYPES):
        stay = stays[(i * 7) % len(stays)]
        t0 = s.ctx.at(fri, 18, 10) + timedelta(minutes=20 * i)
        lang = stay.get("language") or "en"
        text = ORIGINALS.get(lang, {}).get(type_key) or DESCRIPTIONS[type_key]
        key = f"fri:{i}"
        steps.append((t0, "create", {"key": key, "room": stay["room_number"], "type": type_key, "text": text,
                                     "language": lang if text != DESCRIPTIONS[type_key] else "en",
                                     "guest_ref": stay["pms_res_id"]}))
        ack_min, res_min = SLA[type_key]
        who = s.staff_for(DEPT_OF[type_key])
        ack = t0 + timedelta(minutes=rng.choice((6, 7, 8)) if i < 3 else 1)
        steps.append((ack, "acknowledged", {"key": key, "who": who}))
        steps.append((t0 + timedelta(minutes=res_min + rng.randint(12, 30)), "completed", {"key": key, "who": who}))
    return steps


# days before the anchor, hour, minute, source, description, close note (None: still open at the anchor)
L1_CASES = (
    (41, 14, 20, "voice", "Guest collapsed in the bathroom and is not responding, please send help",
     "Ambulance called at 14:24, guest taken to Rashid Hospital; family informed."),
    (9, 23, 10, "phone_desk", "Smoke smell in the corridor on floor 4 near the linen room",
     "Engineering found an overheated extractor motor; isolated, Civil Defence not required."),
    (0, 19, 35, "whatsapp", "My child is locked in the bathroom and cannot open the door, please help fast", None),
)


def l1_steps(s: Svc) -> list[tuple[list, date, dict]]:
    """Each L1 through intake (priority L1: the incident opens beside the
    request, the GM hears at creation). -> [(steps, day, case)]."""
    out = []
    for i, (days, h, m, source, text, note) in enumerate(L1_CASES):
        d = s.ctx.anchor - timedelta(days=days)
        stays = sorted(s.nights.get(d) or [], key=lambda r: r["room_number"])
        if not stays:
            continue
        stay = stays[(i * 5 + 3) % len(stays)]
        t0, key = s.ctx.at(d, h, m), f"l1:{i}"
        who = s.staff_for("front_office")
        steps = [(t0, "create", {"key": key, "room": stay["room_number"], "type": "emergency", "text": text,
                                 "source": source, "priority_hint": "L1", "guest_ref": stay["pms_res_id"]}),
                 (t0 + timedelta(minutes=1), "acknowledged", {"key": key, "who": who, "on_time": True}),
                 # The request is done once the guest is safe; the incident
                 # log stays open until the duty manager files the report.
                 (t0 + timedelta(minutes=38), "completed", {"key": key, "who": who, "on_time": True})]
        out.append((steps, d, {"key": key, "room": stay["room_number"], "note": note, "closed_at": t0 + timedelta(
            minutes=40), "day": d}))
    return out


def close_incident(s: Svc, request_id: int, closed_at: datetime, note: str) -> None:
    """The duty manager closes the incident log entry (what the incident
    screen's close button writes)."""
    from psycopg2.extras import Json

    s.cur.execute(
        """UPDATE svc_incidents SET status = 'closed', closed_at = %s, close_note = %s, closed_by = %s
           WHERE hotel_slug = %s AND request_id = %s AND status = 'open'""",
        (closed_at, note, Json({"type": "user", "label": "Omar Khalid", "role": "duty_manager"}), s.slug,
         request_id),
    )


# kind, AED, description, the role that gave it (within its authority)
GESTURES = (("fnb_credit", 75, "Dinner credit at the coffee shop", "supervisor"),
            ("amenity", 40, "Fruit plate and a note from the duty manager", "staff"),
            ("late_checkout", 0, "Late checkout until 14:00", "supervisor"),
            ("fnb_credit", 120, "Breakfast for two on the house", "duty_manager"))
CATEGORY_OF = {"complaint": "noise", "ac": "maintenance", "plumbing": "maintenance", "ird": "food"}


def recovery_case(s: Svc, request_id: int | None, *, room: str, guest_ref: str | None, category: str,
                  opened_at: datetime, closed_at: datetime | None, gesture: tuple | None, notes: str) -> int:
    """A LEARN recovery case with the gesture given, as the recovery screen
    records it (intake opens the live ones itself)."""
    from psycopg2.extras import Json

    cur = s.cur
    if request_id is not None:
        cur.execute("SELECT id FROM svc_recovery_cases WHERE hotel_slug = %s AND request_id = %s",
                    (s.slug, request_id))
        row = cur.fetchone()
    else:
        row = None
    learn = {"listen": True, "empathize": True, "apologize": True, "resolve": closed_at is not None,
             "notify": closed_at is not None}
    status = "closed" if closed_at else "open"
    if row:
        cid = row["id"]
        cur.execute("UPDATE svc_recovery_cases SET category_key = %s, learn = %s, notes = %s, status = %s, "
                    "closed_at = %s WHERE id = %s", (category, Json(learn), notes, status, closed_at, cid))
    else:
        cur.execute(
            """INSERT INTO svc_recovery_cases (hotel_slug, request_id, room, guest_ref, category_key, severity, status,
                   reason, learn, notes, opened_at, closed_at) VALUES (%s, %s, %s, %s, %s, 'L2', %s, 'complaint', %s,
                   %s, %s, %s) RETURNING id""",
            (s.slug, request_id, room, guest_ref, category, status, Json(learn), notes, opened_at, closed_at),
        )
        cid = cur.fetchone()["id"]
    if gesture:
        kind, aed, desc, role = gesture
        cur.execute(
            """INSERT INTO svc_recovery_actions (hotel_slug, case_id, kind, description, value_minor, currency, status,
                   within_authority, authorized_by, requested_by, created_at, decided_at)
               VALUES (%s, %s, %s, %s, %s, 'AED', 'given', TRUE, %s, %s, %s, %s)""",
            (s.slug, cid, kind, desc, aed * 100, Json({"role": role, "label": f"{role}", "via": "console"}),
             Json({"role": role}), opened_at + timedelta(minutes=25), opened_at + timedelta(minutes=25)),
        )
    s.bump("recovery_cases")
    return cid


# minutes after 17:00 on the anchor, type, source, end state at 21:00. What
# is still open at 21:00 belongs to other departments: Housekeeping (Maria
# and Priya) starts the demo fortnight with no open job, so the reviewer's
# first towels request for Room 204 goes to Maria, the free attendant on
# that floor (05 spec section 4).
TAIL = ((5, "towels", "whatsapp", "completed"), (18, "ird", "voice", "completed"),
        (31, "water", "web", "completed"), (47, "checkout", "phone_desk", "completed"),
        (62, "amenities", "whatsapp", "completed"), (80, "drinks", "voice", "completed"),
        (104, "make_up_room", "web", "completed"), (212, "plumbing", "whatsapp", "acknowledged"),
        (219, "front_desk", "phone_desk", "acknowledged"), (226, "ird", "voice", "acknowledged"),
        (238, "billing", "whatsapp", "new"), (239, "wake_up", "web", "new"))


def tail_steps(s: Svc) -> list[tuple[datetime, str, dict]]:
    """The anchor evening the console opens on: a normal evening, three
    requests in progress and two just in."""
    stays = sorted(s.nights.get(s.ctx.anchor) or [], key=lambda r: r["room_number"])
    steps: list = []
    for i, (mins, type_key, source, end) in enumerate(TAIL if stays else ()):
        stay = stays[(i * 3 + 1) % len(stays)]
        t0, key = s.ctx.at(s.ctx.anchor, 17) + timedelta(minutes=mins), f"tail:{i}"
        lang = stay.get("language") or "en"
        text = ORIGINALS.get(lang, {}).get(type_key) if source in GUEST_SOURCE_KEYS else None
        steps.append((t0, "create", {"key": key, "room": stay["room_number"], "type": type_key, "source": source,
                                     "text": text or DESCRIPTIONS[type_key], "language": lang if text else "en",
                                     "guest_ref": stay["pms_res_id"]}))
        who = s.staff_for(DEPT_OF[type_key])
        if end in ("acknowledged", "completed"):
            steps.append((t0 + timedelta(minutes=2), "acknowledged", {"key": key, "who": who, "on_time": True}))
        if end == "completed":
            steps.append((t0 + timedelta(minutes=SLA[type_key][1] - 3), "completed",
                          {"key": key, "who": who, "on_time": True}))
    return steps


def demo_phone(n: int) -> str:
    """+971 50 000 0nnn: a synthetic mobile block nobody is issued in the
    demo (and notify runs in capture mode locally)."""
    return f"+97150000{n:04d}"


REPEAT_GUEST = ("Lukas Brandt", "DE", "en")


def repeat_room(ctx: Any, s: Svc) -> tuple[str, dict, dict | None] | None:
    """(room, anchor stay, A-4 stay). The AC is the room's fault, not a
    guest's: room 504 (else a nearby room, else any) occupied on day A-4
    and with a guest in house on the anchor who arrived before it (the J5
    day check goes out the morning after arrival). When no room qualifies
    (a small --scale), a direct booking is made in a free room for the
    week and that guest reports both times."""
    a = ctx.anchor
    early, now_ = s.nights.get(a - timedelta(days=4)) or [], s.nights.get(a) or []
    for room in (REPEAT_ROOM, "503", "505", "506", *sorted(ctx.ids.get("rooms") or {})):
        first = next((r for r in early if r["room_number"] == room), None)
        second = next((r for r in now_ if r["room_number"] == room and r["check_in"] < a), None)
        if first and second:
            return room, second, first
    room = free_room(ctx, a - timedelta(days=5), a + timedelta(days=2), prefer=(REPEAT_ROOM, "503", "505", "506"))
    if room is None:
        return None
    stay = ops_stay(ctx, "repeat_ac", channel="DIRECT_WEB", check_in=a - timedelta(days=5), nights=7,
                    status="IN_HOUSE", guest=REPEAT_GUEST, room=room,
                    contacts=[{"kind": "email", "value": "lukas.brandt.demo@example.com"}])
    return room, stay, None


def repeat_fault(ctx: Any, s: Svc, api: Any) -> None:
    """Room 504's AC fails twice in a week: reported on day A-4 through
    WhatsApp and fixed in 50 minutes, then again on the anchor as the
    in-house guest's J5 reply (the anchor timeline files that ticket, the
    guests section records the journey)."""
    a = ctx.anchor
    found = repeat_room(ctx, s)
    if found is None:
        return
    room, stay, first = found
    ctx.stats["ops_repeat_stay"] = stay
    t0 = ctx.at(a - timedelta(days=4), 15, 10)
    who = s.staff_for("engineering")
    run_timeline(s, api, [
        (t0, "create", {"key": "repeat:1", "room": room, "type": "ac", "text": "The AC is not cooling, room is 27 C",
                        "source": "whatsapp", "guest_ref": (first or stay)["pms_res_id"]}),
        (t0 + timedelta(minutes=3), "acknowledged", {"key": "repeat:1", "who": who}),
        (t0 + timedelta(minutes=50), "completed", {"key": "repeat:1", "who": who}),
    ], until=t0 + timedelta(minutes=55))
    plant(ctx, "service.repeat_fault", f"room:{room}:ac", module="service",
          dedupe_key=f"service.repeat_fault:room:{room}:ac",
          subjects=[{"room": room, "pms_res_id": stay["pms_res_id"]}],
          note="AC reported on A-4 and again by the J5 reply on the anchor.")


def complaint_live(ctx: Any, s: Svc, api: Any) -> None:
    """Last night's noise complaint: intake opens the recovery case, the duty
    manager gives a dinner credit within authority and closes it."""
    d = ctx.anchor - timedelta(days=1)
    stays = sorted(s.nights.get(d) or [], key=lambda r: r["room_number"])
    if not stays:
        return
    stay = stays[len(stays) // 2]
    t0 = ctx.at(d, 23, 40)
    got = run_timeline(s, api, [
        (t0, "create", {"key": "complaint:1", "room": stay["room_number"], "type": "complaint",
                        "text": "The room next door is very noisy, music since 11 pm", "source": "voice",
                        "guest_ref": stay["pms_res_id"]}),
        (t0 + timedelta(minutes=3), "acknowledged", {"key": "complaint:1", "who": s.staff_for("front_office")}),
        (t0 + timedelta(minutes=30), "completed", {"key": "complaint:1", "who": s.staff_for("front_office")}),
    ], until=t0 + timedelta(minutes=35))
    row = got.get("complaint:1")
    if row:
        recovery_case(s, row["id"], room=stay["room_number"], guest_ref=stay["pms_res_id"], category="noise",
                      opened_at=t0, closed_at=t0 + timedelta(minutes=45), gesture=GESTURES[3],
                      notes="Security asked the next room to lower the music; breakfast for two offered and accepted.")
        plant(ctx, "service.recovery_case", "noise:last-night", klass="ROW", module="service",
              subjects=[{"room": stay["room_number"], "pms_res_id": stay["pms_res_id"]}],
              note="Complaint via intake opens the case; gesture AED 120 within the duty manager's authority.")


# staff (plain name), shift, shift role, floors: the history roster. Six
# shifts a week each, the day off rotating by position; the Friday evening
# plant is one attendant short (Priya's evening shift is cancelled on
# Fridays). Housekeeping has its leads on the evening and night shifts and
# the front office a duty manager around the clock, so routing finds a
# person (In-Room Dining closes at 23:00, Concierge requests go to the front
# desk). In-Room Dining runs two shift leads per shift, so a lead's day off
# never leaves the dining orders without a person (their days off never
# coincide). Elsewhere the day-off rotation can leave a shift thin, which is
# realistic and shows in the analytics.
ROSTER = (
    ("Joy Dela Cruz", "morning", "attendant", ("1", "2")), ("Anita Gurung", "morning", "attendant", ("3", "4")),
    ("Grace Wanjiru", "morning", "attendant", ("5", "6", "7", "8")),
    ("Maria Santos", "evening", "attendant", ("1", "2", "3", "4")),
    ("Priya Sharma", "evening", "attendant", ("5", "6", "7", "8")),
    ("Rekha Nair", "evening", "supervisor", ()), ("Sunil Perera", "night", "supervisor", ()),
    ("Arjun Menon", "morning", "supervisor", ()), ("Ahmed Farouk", "evening", "supervisor", ()),
    ("Kiran Thapa", "evening", "supervisor", ()), ("Ramesh Pillai", "morning", "supervisor", ()),
    ("Imran Qureshi", "morning", "supervisor", ()), ("Rajesh Kumar", "evening", "supervisor", ()),
    ("Bilal Hassan", "night", "supervisor", ()), ("Omar Khalid", "evening", "duty_manager", ()),
    ("Hessa Al Ali", "morning", "duty_manager", ()), ("Mohammed Rafiq", "night", "duty_manager", ()),
)
SHIFT_HOURS = {"morning": (7, 8), "evening": (15, 8), "night": (23, 8)}
FRIDAY_SHORT = "Priya Sharma"

# ─── The demo fortnight (05 spec section 4) ───
# From the anchor day (the seed's "now" lies on it) for DEMO_DAYS more days
# the deck cast is on shift around the clock, so a reviewer at any hour sees
# the deck's story: a Housekeeping job for Room 204 reaches Maria (floors
# 1-4), floors 5-8 reach Priya, the supervisor is told at 3 minutes and the
# duty manager at 5. The other Housekeeping staff are off (on leave), so no
# one else covers Maria's floors; every other department keeps its shifts,
# with no day off, so routing never falls back. Omar is the only duty
# manager rostered: the front-office leads work as agents.
DEMO_DAYS = 14
# name -> (shift role, floors); department from the staff row.
DECK_ON_CALL = {
    "Maria Santos": ("attendant", ("1", "2", "3", "4")),
    "Priya Sharma": ("attendant", ("5", "6", "7", "8")),
    "Rekha Nair": ("supervisor", ()),
    "Omar Khalid": ("duty_manager", ()),
}
DEMO_LEAVE = frozenset({"Joy Dela Cruz", "Anita Gurung", "Grace Wanjiru", "Sunil Perera"})
DEMO_AGENTS = frozenset({"Hessa Al Ali", "Mohammed Rafiq"})


def seed_roster(ctx: Any, s: Svc, live_days: set[date]) -> int:
    """The published roster from the first history day to DEMO_DAYS after
    the anchor, so routing finds people on shift (without it every request
    falls back and raises service.understaffed). Before the anchor days off
    rotate, except on the days the live episodes replay, which run on a full
    roster (the Friday plant aside), so routing never falls back in the
    story the owner reads. From the anchor on it is the demo fortnight
    (DECK_ON_CALL above)."""
    from psycopg2.extras import execute_values

    staff, depts = ctx.ids.get("staff") or {}, ctx.ids.get("staff_departments") or {}
    rows = []
    d = ctx.history_start
    while d <= ctx.anchor + timedelta(days=DEMO_DAYS):
        fortnight = d >= ctx.anchor
        for name, (role, floors) in (DECK_ON_CALL.items() if fortnight else ()):
            sid = staff.get(name)
            if sid is not None:
                rows.append((ctx.slug, sid, d, ctx.at(d, 0), ctx.at(d + timedelta(days=1), 0), depts.get(name),
                             list(floors), role))
        for i, (name, shift, role, floors) in enumerate(ROSTER):
            sid = staff.get(name)
            if sid is None:
                continue
            if fortnight:
                if name in DECK_ON_CALL or name in DEMO_LEAVE:
                    continue
                role = "attendant" if name in DEMO_AGENTS else role
            elif (d.toordinal() + i) % 7 == 0 and d not in live_days:
                continue
            elif name == FRIDAY_SHORT and d.weekday() == 4:
                continue
            h, length = SHIFT_HOURS[shift]
            start = ctx.at(d, h)
            rows.append((ctx.slug, sid, d, start, start + timedelta(hours=length), depts.get(name), list(floors),
                         role))
        d += timedelta(days=1)
    execute_values(ctx.cur, "INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, "
                            "end_at, department_key, floors, shift_role) VALUES %s", rows, page_size=2000)
    return len(rows)


def seed_service(ctx: Any) -> dict:
    """History and live episodes in time order: before each live episode the
    history rows older than it are written, so the sweep's "last 8 hours"
    and the merge window see the world as it was then, never later rows."""
    api = ctx.module_api("service")
    if api is None or not ctx.ids.get("reservations"):
        return {"service_requests": 0}
    s = Svc(ctx)
    fri = last_friday(ctx.anchor)
    live_days = {fri, ctx.anchor, ctx.anchor - timedelta(days=1), ctx.anchor - timedelta(days=4),
                 *(ctx.anchor - timedelta(days=c[0]) for c in L1_CASES)}
    s.bump("roster_shifts", seed_roster(ctx, s, live_days))
    # The last Friday evening is played live (intake + sweep), not written.
    plan = [p for p in plan_history(s, ctx.history_start, ctx.anchor - timedelta(days=1))
            if not (p[0].date() == fri and p[0].hour >= 18)]
    pending = [history_row(s, t, stay, type_key, breach=b) for t, stay, type_key, b in plan]
    episodes: list[tuple[datetime, Any]] = [(ctx.at(fri, 18, 0), lambda: friday_episode(ctx, s, api, fri))]
    tail, tail_l1 = tail_steps(s), []
    for steps, d, case in l1_steps(s):
        if d == ctx.anchor:  # one timeline per day: the sweep never runs backwards
            tail += steps
            tail_l1.append(case)
        else:
            episodes.append((steps[0][0], lambda st=steps, c=case: l1_episode(ctx, s, api, st, c)))
    episodes.append((ctx.at(ctx.anchor - timedelta(days=4), 15, 0), lambda: repeat_fault(ctx, s, api)))
    episodes.append((ctx.at(ctx.anchor - timedelta(days=1), 23, 30), lambda: complaint_live(ctx, s, api)))
    episodes.append((ctx.at(ctx.anchor, 11, 0), lambda: tail_episode(ctx, s, api, tail, tail_l1)))
    episodes += upsell_episodes(ctx, s)
    i = 0
    for start, fn in sorted(episodes, key=lambda e: e[0]):
        j = i
        while j < len(pending) and pending[j]["timestamp"] < ms(start):
            j += 1
        write_history(s, pending[i:j])
        i = j
        fn()
    write_history(s, pending[i:])
    ctx.stats["ops_svc"] = s
    # What the minute sweep does on its next run: emergencies handled weeks
    # ago (incident closed above with a raw UPDATE) leave the brief that
    # growth builds next, and only tonight's open one stays on it.
    with at(ctx, ctx.now):
        api.resolve_finished_signals(ctx.cur, ctx.slug)
    for k in range(35):  # the rollups the 00:30 job keeps (last 5 weeks)
        with at(ctx, ctx.at(ctx.anchor - timedelta(days=k), 0, 30)):
            run_job(ctx, "service.daily_rollup")
    return {"service_requests": s.counts.get("service_history", 0) + s.counts.get("service_live", 0),
            "service_live": s.counts.get("service_live", 0), "recovery_cases": s.counts.get("recovery_cases", 0),
            "roster_shifts": s.counts.get("roster_shifts", 0)}


def write_history(s: Svc, rows: list[dict]) -> None:
    ids = insert_history(s, rows)
    s.bump("service_history", len(ids))
    for rid, r in zip(ids, rows):
        if r["request_type_key"] == "complaint":
            t = datetime.fromtimestamp(r["timestamp"] / 1000, UTC)
            recovery_case(s, rid, room=r["room"], guest_ref=r["guest_ref"], category="noise", opened_at=t,
                          closed_at=t + timedelta(hours=2), gesture=s.rng.choice(GESTURES + (None,)),
                          notes="Moved the neighbours' late check-in party to a quieter floor.")


def friday_episode(ctx: Any, s: Svc, api: Any, fri: date) -> None:
    made = run_timeline(s, api, friday_steps(s, fri), until=ctx.at(fri, 23, 30))
    ctx.stats["ops_friday"] = made
    plant(ctx, "service.sla_breach_cluster", f"friday-evening:{fri}", module="service",
          subjects=[{"business_date": fri.isoformat(), "requests": len(made)}],
          note="Friday evenings carry the SLA breaches (history ~36% vs ~4%); the last Friday is live.")


J5_TEXT = "AC is not cooling in {room}, it is very hot"


def j5_steps(ctx: Any, s: Svc) -> list[tuple[datetime, str, dict]]:
    """The repeat-AC guest answers the anchor morning's J5 day check with a
    complaint. The journeys reply handler files exactly this request
    (service.api.create_request, source whatsapp_journey); the guests
    section records the J5 plan and reply that point at it. Engineering
    fixes it within the hour."""
    stay = ctx.stats.get("ops_repeat_stay")
    if not stay:
        return []
    t0, who = ctx.at(ctx.anchor, 11, 20), s.staff_for("engineering")
    return [(t0, "create", {"key": "j5:ac", "room": stay["room_number"], "type": "ac", "source": "whatsapp_journey",
                            "text": J5_TEXT.format(room=stay["room_number"]), "guest_ref": stay["pms_res_id"]}),
            (t0 + timedelta(minutes=3), "acknowledged", {"key": "j5:ac", "who": who, "on_time": True}),
            (t0 + timedelta(minutes=45), "completed", {"key": "j5:ac", "who": who, "on_time": True})]


def tail_episode(ctx: Any, s: Svc, api: Any, steps: list, l1_cases: list[dict]) -> None:
    made = run_timeline(s, api, steps + j5_steps(ctx, s), until=ctx.now)
    if "j5:ac" in made:
        ctx.stats["ops_j5_request"] = made["j5:ac"]
    for case in l1_cases:
        l1_plant(ctx, case)


def l1_episode(ctx: Any, s: Svc, api: Any, steps: list, case: dict) -> None:
    until = min(steps[-1][0] + timedelta(minutes=10), ctx.now)
    got = run_timeline(s, api, steps, until=until)
    row = got.get(case["key"])
    if row and case["note"]:
        close_incident(s, row["id"], case["closed_at"], case["note"])
    l1_plant(ctx, case)


def l1_plant(ctx: Any, case: dict) -> None:
    plant(ctx, "service.l1_incident", f"l1:{case['day']}", module="service",
          subjects=[{"room": case["room"], "day": case["day"].isoformat(), "open": case["note"] is None}],
          note="L1 via intake: incident beside the request, GM signal at creation.")


# ─── Amenities ───

# The part of the UAE default catalog the demo's issuance log uses (key,
# name EN, name AR, category, basis, std qty, unit cost fils, single-use
# plastic, aliases). amenities.api has no door to its catalog seeding yet
# (FOUNDATION-REQUEST in core_catalog), so when the catalog is empty the
# seed writes these rows itself (source DEMO); the owner's first catalog
# visit then adds the remaining defaults with needs_review.
AMN_ITEMS = (
    ("SHAMPOO", "Shampoo 30 ml", "شامبو ٣٠ مل", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "1", 120, False,
     ["shampoo", "شامبو"]),
    ("SOAP_BAR", "Soap bar 30 g", "صابونة ٣٠ غ", "BATH", "PER_OCCUPIED_ROOM_NIGHT", "1", 80, False,
     ["soap", "صابون", "صابونة"]),
    ("WATER_500", "Water 500 ml", "مياه ٥٠٠ مل", "WATER", "PER_OCCUPIED_ROOM_NIGHT", "2", 60, False,
     ["water", "bottled water", "مياه", "ماء"]),
    ("SLIPPERS", "Slippers (pair)", "خف (زوج)", "SLIPPERS", "PER_ARRIVAL", "2", 600, False,
     ["slippers", "slipper", "شبشب", "خف"]),
    ("DENTAL_KIT", "Dental kit", "طقم أسنان", "DENTAL", "ON_REQUEST", "0", 150, True,
     ["toothbrush", "toothpaste", "dental kit", "dental", "فرشاة أسنان"]),
    ("SHAVING_KIT", "Shaving kit", "طقم حلاقة", "SHAVING", "ON_REQUEST", "0", 250, True,
     ["razor", "shaving kit", "shaving", "ماكينة حلاقة"]),
    ("SHOWER_CAP", "Shower cap", "غطاء الاستحمام", "BATH", "ON_REQUEST", "0", 40, True,
     ["shower cap", "غطاء شعر"]),
    ("SEWING_KIT", "Sewing kit", "طقم خياطة", "OTHER", "ON_REQUEST", "0", 70, False, ["sewing kit", "needle"]),
)
# What an "amenities" request asked for: description, [(item key, qty)].
AMENITY_ASKS = (
    ("Need a dental kit please", [("DENTAL_KIT", 1)]),
    ("Two dental kits for room please", [("DENTAL_KIT", 2)]),
    ("Can I get a shaving kit", [("SHAVING_KIT", 1)]),
    ("Need a dental kit and a shaving kit", [("DENTAL_KIT", 1), ("SHAVING_KIT", 1)]),
    ("Two shower caps please", [("SHOWER_CAP", 2)]),
    ("Please send a sewing kit", [("SEWING_KIT", 1)]),
)
ASK_ITEMS = {d: lines for d, lines in AMENITY_ASKS}
ISSUANCE_DAYS = 70          # the first rollup covers 63 days (current week + 8 history windows)
DENTAL_FLOOR, DENTAL_DAYS, DENTAL_NIGHT_EXTRA = "6", 10, 12
SLIPPER_CART, SLIPPER_DAYS, SLIPPER_EXTRA = "HK Cart 3F", 9, 9
SHAVING_GAP_DAYS, SHAVING_GAP_RATIO = 14, 2.2
FILL_MONDAYS = 2            # the closet-fill decoy: the last two Mondays


def ensure_catalog(ctx: Any, api: Any) -> dict[str, dict]:
    """{key: item row} for AMN_ITEMS, writing the missing ones (see above)."""
    from psycopg2.extras import execute_values

    have = {i["key"]: i for i in api.list_items(ctx.cur, ctx.slug, active_only=False)}
    missing = [r for r in AMN_ITEMS if r[0] not in have]
    if missing:
        rows = execute_values(ctx.cur, """
            INSERT INTO amn_items (hotel_slug, key, name_en, name_ar, category, basis, std_qty, unit_cost_minor,
                currency, is_single_use_plastic, aliases, request_type_key, source, sort)
            VALUES %s RETURNING id, unit_cost_minor""", [
            (ctx.slug, k, en, ar, cat, basis, std, cost, "AED", sup, aliases, "amenities", "DEMO", 10 * n)
            for n, (k, en, ar, cat, basis, std, cost, sup, aliases) in enumerate(missing)], fetch=True)
        # One cost row dated far back, as catalog seeding writes it: every
        # history day is costed at the catalog price.
        execute_values(ctx.cur, "INSERT INTO amn_item_costs (hotel_slug, item_id, effective_from, unit_cost_minor, "
                                "currency, source) VALUES %s ON CONFLICT DO NOTHING",
                       [(ctx.slug, r["id"], date(2000, 1, 1), r["unit_cost_minor"], "AED", "DEMO") for r in rows])
        have = {i["key"]: i for i in api.list_items(ctx.cur, ctx.slug, active_only=False)}
    ctx.ids["amenity_items"] = {k: v["id"] for k, v in have.items()}
    return have


def ensure_locations(ctx: Any) -> dict[str, dict]:
    """The main store, one linen closet per floor and one cart per two floors."""
    floors = sorted({str(r.get("floor")) for r in (ctx.ids.get("rooms") or {}).values()})
    want = [("STORE", None, "Main store")] + [("FLOOR_CLOSET", f, f"Floor {f} closet") for f in floors]
    want += [("CART", f, f"HK Cart {f}F") for f in floors if int(f) % 2 == 1]
    out = {}
    for kind, floor, name in want:
        ctx.cur.execute(
            """INSERT INTO amn_locations (hotel_slug, kind, floor, name) VALUES (%s, %s, %s, %s)
               ON CONFLICT (hotel_slug, name) DO UPDATE SET kind = EXCLUDED.kind RETURNING id, kind, floor, name""",
            (ctx.slug, kind, floor, name))
        out[name] = dict(ctx.cur.fetchone())
    return out


def link_amenity_requests(ctx: Any, api: Any, items: dict[str, dict], start: date) -> list[dict]:
    """Every "amenities" request since `start` gets its item lines through
    amenities.api.link_request (TOOL: intake named the item), so expected
    use for on-request items is the requests the guests actually made.
    -> [{request_id, t, floor, key, qty}]."""
    t0 = ms(ctx.at(start, 0))
    ctx.cur.execute(
        """SELECT id, "timestamp", floor, description FROM staff_requests WHERE hotel_slug = %s
           AND request_type_key = 'amenities' AND "timestamp" >= %s AND state <> 'cancelled' ORDER BY id""",
        (ctx.slug, t0))
    out = []
    for r in ctx.cur.fetchall():
        for key, qty in ASK_ITEMS.get(r["description"], ()):
            if key in items:
                api.link_request(ctx.cur, ctx.slug, request_id=int(r["id"]), amenity_item_id=int(items[key]["id"]),
                                 qty=qty)
                out.append({"request_id": int(r["id"]), "t": datetime.fromtimestamp(r["timestamp"] / 1000, UTC),
                            "floor": r["floor"], "key": key, "qty": qty})
    return out


# The morning attendants who restock the floor closets (the history roster).
FLOOR_ATTENDANT = {"1": "Joy Dela Cruz", "2": "Joy Dela Cruz", "3": "Anita Gurung", "4": "Anita Gurung",
                   "5": "Grace Wanjiru", "6": "Grace Wanjiru", "7": "Grace Wanjiru", "8": "Grace Wanjiru"}


def _line(t: datetime, key: str, qty: int, floor: str | None, *, reason: str, staff: str | None,
          location: str | None = None, request_ref: int | None = None, shift: str | None = None) -> dict:
    local = t.astimezone(t.tzinfo)
    return {"issued_at": local.isoformat(), "item": key, "qty": qty, "floor": floor or "",
            "shift": shift or shift_of(local.hour), "staff_name": f"{staff}" if staff else "",
            "reason": reason, "location": location or "", "request_ref": request_ref or ""}


def issuance_plan(ctx: Any, links: list[dict], start: date) -> tuple[list[dict], dict]:
    """The housekeeping issuance log from `start` to yesterday, with the
    plants. -> (rows, facts for the manifest)."""
    rng, a = ctx.rng("ops:amenities"), ctx.anchor
    rooms = ctx.ids.get("rooms") or {}
    nights = stays_by_day(ctx)
    rows: list[dict] = []
    mondays = sorted({a - timedelta(days=k) for k in range(1, 15) if (a - timedelta(days=k)).weekday() == 0},
                     reverse=True)[:FILL_MONDAYS]
    d = start
    while d < a:
        occ: dict[str, int] = {}
        arr: dict[str, int] = {}
        for r in nights.get(d) or []:
            f = str((rooms.get(r["room_number"]) or {}).get("floor") or r["room_number"][:-2])
            occ[f] = occ.get(f, 0) + 1
            if r["check_in"] == d:
                arr[f] = arr.get(f, 0) + 1
        for f in sorted(occ):
            staff = FLOOR_ATTENDANT.get(f)
            t = ctx.at(d, 9, 15)
            for key, per in (("SHAMPOO", 1), ("SOAP_BAR", 1)):
                qty = occ[f] * per + rng.choice((0, 0, 0, 0, 1, -1))
                if qty > 0:
                    rows.append(_line(t, key, qty, f, reason="restock", staff=staff, location=f"Floor {f} closet"))
            # The closet-fill decoy: on the last two Mondays the floor
            # closets get Monday's and Tuesday's water in one go (a normal
            # 7-day sum, a doubled day), and Tuesday issues none.
            if d in mondays:
                nxt = sum(1 for r in nights.get(d + timedelta(days=1)) or []
                          if str((rooms.get(r["room_number"]) or {}).get("floor")) == f)
                rows.append(_line(t, "WATER_500", 2 * (occ[f] + nxt), f, reason="restock", staff=staff,
                                  location=f"Floor {f} closet"))
            elif (d - timedelta(days=1)) not in mondays:
                rows.append(_line(t, "WATER_500", 2 * occ[f], f, reason="restock", staff=staff,
                                  location=f"Floor {f} closet"))
            if arr.get(f):
                cart = f"HK Cart {f if int(f) % 2 else int(f) - 1}F"
                rows.append(_line(ctx.at(d, 13, 40), "SLIPPERS", 2 * arr[f], f, reason="restock", staff=staff,
                                  location=cart))
        d += timedelta(days=1)
    for ln in links:  # on-request items, handed over against the logged request
        f = str(ln["floor"] or "")
        rows.append(_line((ln["t"] + timedelta(minutes=12)).astimezone(ctx.at(a).tzinfo), ln["key"], ln["qty"], f,
                          reason="guest", staff=FLOOR_ATTENDANT.get(f), request_ref=ln["request_id"]))
    return rows, plant_issuance(ctx, rng, rows, links)


def plant_issuance(ctx: Any, rng: Any, rows: list[dict], links: list[dict]) -> dict:
    """Append the planted lines to `rows`. -> facts for the manifest."""
    a = ctx.anchor
    # Dental kits: floor 6's night shift hands out 12 a night for the last 10
    # nights with no request behind them (about 3x the hotel's usual daily
    # dental kits): a 7-day loss over AED 100 that occupancy does not explain.
    for k in range(DENTAL_DAYS, 0, -1):
        d = a - timedelta(days=k)
        rows.append(_line(ctx.at(d, 23, 40), "DENTAL_KIT", DENTAL_NIGHT_EXTRA, DENTAL_FLOOR, reason="guest",
                          staff="Sunil Perera", shift="night"))
    # Slippers: 9 extra pairs a day loaded onto one cart for the last 9 days.
    for k in range(SLIPPER_DAYS, 0, -1):
        rows.append(_line(ctx.at(a - timedelta(days=k), 16, 5), "SLIPPERS", SLIPPER_EXTRA, "3", reason="restock",
                          staff="Anita Gurung", location=SLIPPER_CART))
    # Shaving kits: over the last 14 days 2.2x the logged requests go out
    # (at least 10 in all): kits handed over on a verbal ask, never logged.
    lo = ctx.at(a - timedelta(days=SHAVING_GAP_DAYS), 0)
    logged = sum(ln["qty"] for ln in links if ln["key"] == "SHAVING_KIT" and ln["t"] >= lo)
    extra = max(10 - logged, int(logged * (SHAVING_GAP_RATIO - 1) + 0.999), 1)
    for n in range(extra):
        d = a - timedelta(days=1 + (n * 13) % SHAVING_GAP_DAYS)
        f = str(1 + (n * 3) % 8)
        rows.append(_line(ctx.at(d, 10 + n % 9, 20), "SHAVING_KIT", 1, f, reason="guest",
                          staff=FLOOR_ATTENDANT.get(f)))
    return {"dental_units": DENTAL_DAYS * DENTAL_NIGHT_EXTRA, "slipper_pairs": SLIPPER_DAYS * SLIPPER_EXTRA,
            "shaving_logged": logged, "shaving_unlogged": extra}


ISSUANCE_COLS = ("issued_at", "item", "qty", "floor", "shift", "staff_name", "reason", "location", "request_ref")


def import_issuance(ctx: Any, rows: list[dict]) -> dict:
    """The housekeeping issuance export (CSV) through the real pipeline:
    clefs.files, core.ingest staging (alias mapping, no template), then the
    amenities ISSUANCE commit handler."""
    from clefs import files
    from clefs.core import ingest

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(ISSUANCE_COLS), lineterminator="\n")
    w.writeheader()
    for r in sorted(rows, key=lambda r: (r["issued_at"], r["item"], r["floor"])):
        w.writerow(r)
    data = buf.getvalue().encode("utf-8")
    owner = (ctx.ids.get("users") or {}).get("owner")
    ref = files.put_file(ctx.cur, ctx.slug, data, filename="housekeeping_issuance_log.csv", content_type="text/csv",
                         purpose="ingest.upload", uploaded_by=owner)
    batch = ingest.create_batch(ctx.cur, ctx.slug, file_id=ref.id, kind="ISSUANCE", via="DEMO", uploaded_by=owner,
                                use_ai=False)
    if batch["status"] != "REVIEW" or batch.get("gate_blocking"):
        raise RuntimeError(f"ops seed: issuance log did not stage: {batch['status']} {batch.get('errors')}")
    out = ingest.commit_batch(ctx.cur, ctx.slug, batch["id"])
    return out.get("commit_result") or {}


def seed_amenities(ctx: Any) -> dict:
    api = ctx.module_api("amenities")
    if api is None or not ctx.table_exists("amn_issuance") or not ctx.ids.get("reservations"):
        return {"amenity_lines": 0}
    items = ensure_catalog(ctx, api)
    ensure_locations(ctx)
    start = ctx.anchor - timedelta(days=ISSUANCE_DAYS)
    links = link_amenity_requests(ctx, api, items, start)
    rows, facts = issuance_plan(ctx, links, start)
    result = import_issuance(ctx, rows)
    # The 03:30 job on the anchor: alias pass, the first rollup (63 days)
    # and every detector for yesterday.
    with at(ctx, ctx.at(ctx.anchor, 3, 30)):
        run_job(ctx, "am.variance_rollup")
    plant(ctx, "am.variance.spike", "dental-floor6-night", module="amenities",
          subjects=[{"item_key": "DENTAL_KIT", "floor": DENTAL_FLOOR, "shift": "night", "days": DENTAL_DAYS,
                     "units": facts["dental_units"]}],
          note="Floor 6 night shift issues dental kits at ~3x for 10 nights with no request behind them.")
    plant(ctx, "am.variance.spike", "slippers-one-cart", module="amenities",
          subjects=[{"item_key": "SLIPPERS", "location": SLIPPER_CART, "pairs": facts["slipper_pairs"]}],
          note="Slippers over-issued to one cart (9 extra pairs a day for 9 days).")
    plant(ctx, "am.request_logging_gap", "shaving-kits", module="amenities", dedupe_key="am.request_logging_gap",
          subjects=[{"item_key": "SHAVING_KIT", "logged": facts["shaving_logged"],
                     "unlogged": facts["shaving_unlogged"]}],
          note="Shaving kits handed out 2.2x the logged requests over 14 days.")
    plant(ctx, "am.sup_compliance", "single-use-plastic", module="amenities", dedupe_key="am.sup_compliance",
          subjects=[{"item_key": k} for k in ("DENTAL_KIT", "SHAVING_KIT", "SHOWER_CAP")],
          note="Plastic dental, shaving and shower-cap kits issued under the Dubai single-use plastic policy.")
    plant(ctx, "am.variance.spike", "closet-fill-monday", klass="DECOY", module="amenities",
          subjects=[{"item_key": "WATER_500", "mondays": FILL_MONDAYS}],
          note="Monday two-day closet fill: a doubled Monday, an empty Tuesday, a normal week. Must not fire.")
    return {"amenity_lines": result.get("written", 0), "amenity_links": len(links)}


# ─── Reviews ───
# The sources' own rows (what store.ingest writes for a Google fetch, a
# Tripadvisor poll and a Booking.com CSV upload: the CSV importer lives
# behind the console route, api.py has no door), then the registered
# reviews.analyze job does everything else: themes (the keyword lexicon;
# the seed answers no model call), the Service link, negative-review
# signals, drafted replies and their dial actions.

# (title, text) by language and polarity; the words are the ones the theme
# lexicon reads (clean, staff, location, breakfast, noise, AC, wifi, value).
REVIEW_TEXT = {
    ("en", True): (("Great value in Deira", "Clean room, friendly staff and the metro is two minutes away. Great "
                    "location for the Gold Souk."),
                   ("Would stay again", "Staff at the front desk were very helpful and the room was spotless. Good "
                    "breakfast."),
                   ("Good base for business", "Quiet room, fast wifi, easy taxi to DWTC. Good value for money.")),
    ("en", False): (("Noisy at night", "The room was clean but very noisy at night from the street. Staff moved us "
                     "the next day."),
                    ("AC problems", "The air conditioning was weak and the room was hot. Maintenance came but it did "
                     "not help much."),
                    ("Slow service", "Asked for towels twice and waited nearly an hour. Staff were polite but slow.")),
    ("ar", True): (("إقامة ممتازة", "الغرفة نظيفة والموظفون متعاونون جدا والموقع قريب من المترو."),
                   ("خدمة رائعة", "الإفطار جيد والاستقبال سريع. أنصح بالفندق.")),
    ("ar", False): (("مزعج", "الغرفة نظيفة لكن الضوضاء في الليل كانت مزعجة جدا."),
                    ("التكييف", "التكييف لا يعمل بشكل جيد والغرفة حارة.")),
    ("ru", True): (("Отличное расположение", "Чистый номер, вежливый персонал, метро рядом."),
                   ("Хороший завтрак", "Завтрак хороший, персонал приветливый, номер чистый.")),
    ("ru", False): (("Шумно", "Номер чистый, но ночью очень шумно."),
                    ("Кондиционер", "Кондиционер плохо охлаждал, в номере было жарко.")),
}
# platform, source kind, scale, count, language weights (en, ar, ru)
REVIEW_PLAN = (("google", "serpapi_google", 5, 32, (20, 8, 4)), ("tripadvisor", "tripadvisor", 5, 12, (9, 0, 3)),
               ("booking", "csv", 10, 22, (14, 4, 4)))
REVIEW_DAYS, REPLY_WINDOW_DAYS, NEGATIVE_SHARE = 120, 14, 0.22
LINKED_REVIEW = ("Waited an hour for towels on Friday night", "Friday night we asked for towels twice and waited "
                 "almost an hour. Room {room}. The staff apologised but the evening shift was clearly short.")


def review_sources(ctx: Any) -> dict[str, int]:
    out = {}
    for platform, kind, _scale, _n, _w in REVIEW_PLAN:
        label = {"google": "Google", "tripadvisor": "Tripadvisor", "booking": "Booking.com CSV"}
        ctx.cur.execute(
            "INSERT INTO rev_sources (hotel_slug, kind, platform, label, last_synced_at) VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id", (ctx.slug, kind, platform, label[platform], ctx.now))
        out[platform] = ctx.cur.fetchone()["id"]
    return out


def review_rows(ctx: Any, rng: Any, sources: dict[str, int]) -> list[tuple]:
    rows, a = [], ctx.anchor
    for platform, _kind, scale, n, weights in REVIEW_PLAN:
        for i in range(n):
            lang = rng.choices(("en", "ar", "ru"), weights=weights, k=1)[0]
            positive = rng.random() >= NEGATIVE_SHARE
            title, text = rng.choice(REVIEW_TEXT[(lang, positive)])
            stars = rng.choice((4, 5, 5)) if positive else rng.choice((2, 3))
            rating = stars if scale == 5 else round(stars * 2 - rng.choice((0, 0.4, 0.8)), 1)
            if platform == "booking":
                text = f"Liked: {text}" if positive else f"Disliked: {text}"
            posted = ctx.at(a - timedelta(days=rng.randrange(1, REVIEW_DAYS)), rng.randrange(8, 23), rng.randrange(60))
            rows.append(_review(ctx, sources[platform], platform, f"demo-{platform}-{i:03d}", title, text, rating,
                                scale, lang, posted, rng))
    return rows


def _review(ctx: Any, source_id: int, platform: str, ext: str, title: str, text: str, rating: float, scale: int,
            lang: str, posted: datetime, rng: Any, room_hint: str | None = None) -> tuple:
    """One rev_reviews row as store.ingest leaves it: /10 halved, raw text
    kept 30 days, the room hint read from the text, an owner response on
    the older ones (the hotel answered them on the platform before Clefs)."""
    old = posted < ctx.at(ctx.anchor - timedelta(days=REPLY_WINDOW_DAYS))
    ingested = posted + timedelta(hours=rng.randrange(2, 20))
    first = ["Mr", "Ms"][rng.randrange(2)] + " " + "ABCDEFGHJKLMNPRSTVW"[rng.randrange(19)] + "."
    return (ctx.slug, source_id, platform, ext, f"{first}", title, rating, scale,
            round(rating / 2, 1) if scale == 10 else rating, lang, posted, text, ingested + timedelta(days=30),
            old, posted + timedelta(days=2) if old else None, ingested, room_hint)


def seed_reviews(ctx: Any) -> dict:
    api = ctx.module_api("reviews")
    if api is None or not ctx.table_exists("rev_reviews"):
        return {"reviews": 0}
    from psycopg2.extras import execute_values

    rng = ctx.rng("ops:reviews")
    sources = review_sources(ctx)
    rows = review_rows(ctx, rng, sources)
    # The negative review that names a Friday-evening request's room: the
    # analysis links it to that Service request (same room, department and
    # the 14 days before the review).
    fri = (ctx.stats.get("ops_friday") or {}).get("fri:0")
    if fri:
        title, text = LINKED_REVIEW
        rows.append(_review(ctx, sources["google"], "google", "demo-google-linked", title,
                            text.format(room=fri["room"]), 2, 5, "en", ctx.at(ctx.anchor - timedelta(days=1), 10, 5),
                            rng, room_hint=fri["room"]))
    execute_values(ctx.cur, """
        INSERT INTO rev_reviews (hotel_slug, source_id, platform, external_id, author_display, title, rating,
            rating_scale, rating_norm_5, language, posted_at, raw_text, raw_expires_at, has_owner_response,
            owner_response_at, ingested_at, room_hint) VALUES %s""", rows, page_size=500)
    # The 15-minute job, run until nothing is pending (10 reviews a run).
    runs = 0
    with at(ctx, ctx.now):
        while runs < 40:
            got = run_job(ctx, "reviews.analyze") or {}
            runs += 1
            if not any(v for k, v in got.items() if isinstance(v, int)):
                break
    if fri:
        plant(ctx, "reviews.linked_request", "friday-towels", klass="ROW", module="reviews",
              subjects=[{"external_id": "demo-google-linked", "room": fri["room"]}],
              note="A 2-star Google review names the room of a Friday-evening request; analysis links it.")
        plant(ctx, "reviews.negative_review", "friday-towels", module="reviews",
              subjects=[{"external_id": "demo-google-linked"}], note="Negative review signal with the draft reply.")
    plant(ctx, "reviews.drafted_replies", "recent-unanswered", klass="ROW", module="reviews",
          subjects=[{"days": REPLY_WINDOW_DAYS}], note="Every recent review without an owner reply has a draft.")
    return {"reviews": len(rows), "review_analyze_runs": runs}


# ─── Guests and journeys ───
# Contacts and consent go through guests.api.record_own_contact (the same
# checks as the registration card and the J1 page) and guests.api.opt_out.
# Journey sends are history: the planner consumes stay events only after
# they commit (core/stays dispatch reads committed transactions), and the
# dispatcher sends at the real clock, so the sends and replies of the last
# weeks are written as the rows the dispatcher and reply handler leave
# (gst_journey_plans, gst_journey_replies, gst_feedback). The J5 ticket and
# the J7 follow-up are the real service.api calls those handlers make.

CONSENT_VERSION = "gst.v1"
# a 1x1 PNG: the guest's signature image on a staff attestation (the demo
# never stores a real signature or any ID field)
SIGNATURE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _actor() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(kind="system", user_id=None, device_id=None, label="demo-seed")


def capture(ctx: Any, api: Any, stay: dict, *, source: str, phone: str, consents: tuple[str, ...],
            evidence: dict | None = None) -> int:
    """guests.api.record_own_contact with transfer consent and the wording
    version the guest was shown."""
    grants = {"transfer_abroad": True, **{c: True for c in consents}}
    return api.record_own_contact(
        ctx.cur, ctx.slug, reservation_id=stay["id"], room=stay.get("room_number"), kind="phone", value=phone,
        capture_source=source, consents=grants, consent_text_versions={k: CONSENT_VERSION for k in grants},
        evidence={"language": "en", **(evidence or {})}, actor=_actor())


def registration_card(ctx: Any, api: Any, stay: dict, phone: str) -> int:
    """The front desk's staff attestation with the guest's signature image
    (the contact-and-consent block only, no ID fields)."""
    from clefs import files

    ref = files.put_file(ctx.cur, ctx.slug, SIGNATURE_PNG, filename="regcard-signature.png",
                         content_type="image/png", purpose="guests.contact_evidence")
    return capture(ctx, api, stay, source="registration_card", phone=phone,
                   consents=("whatsapp_updates", "marketing_whatsapp"),
                   evidence={"kind": "staff_attestation", "signature_file_id": str(ref.id)})


def pick_stay(ctx: Any, *, channel: str, check_in: tuple[int, int] | None = None,
              check_out: tuple[int, int] | None = None,
          in_house: bool = False, skip: set | None = None) -> dict | None:
    """A core stay by channel and dates relative to the anchor (days)."""
    a = ctx.anchor
    for r in ctx.ids.get("reservations") or []:
        if r["channel"] != channel or r["status"] in ("CANCELLED", "NO_SHOW") or r["pms_res_id"] in (skip or ()):
            continue
        if in_house and not (r["check_in"] <= a < r["check_out"]):
            continue
        if check_out and not (a + timedelta(days=check_out[0]) <= r["check_out"] <= a + timedelta(days=check_out[1])):
            continue
        if check_in and not (a + timedelta(days=check_in[0]) <= r["check_in"] <= a + timedelta(days=check_in[1])):
            continue
        return r
    return None


JOURNEY_WINDOW = (30, 7)  # stays leaving from A-30 and arriving up to A+7


def _plan(ctx: Any, rows: list, stay: dict, step: str, due: datetime, status: str, *, channel: str = "email",
          point: int | None = None, skip: str | None = None, error: str | None = None, attempt: int = 1) -> None:
    rows.append((ctx.slug, stay["id"], step, due, due + timedelta(days=1), status, skip, channel, point, error,
                 attempt, due if status not in ("planned", "skipped") else None))


def journey_rows(ctx: Any, rng: Any, special: set[str]) -> list:
    """Sends for recent stays (core stays near the anchor carry an email):
    J1 at booking, J4 on the arrival morning, J5 the day after arrival,
    J7 after check-out; Expedia's J7 is skipped because its alias expired
    at check-out. Statuses come as the dispatcher and receipts leave them."""
    a, now, rows = ctx.anchor, ctx.now, []
    lo, hi = a - timedelta(days=JOURNEY_WINDOW[0]), a + timedelta(days=JOURNEY_WINDOW[1])
    for r in ctx.ids.get("reservations") or []:
        if r["status"] == "CANCELLED" or r["pms_res_id"] in special or r["check_out"] < lo or r["check_in"] > hi:
            continue
        booked = r["booked_at"] if isinstance(r["booked_at"], datetime) else ctx.at(r["check_in"] - timedelta(days=14))
        if booked >= ctx.at(lo - timedelta(days=21)):
            _plan(ctx, rows, r, "j1", booked + timedelta(minutes=6), rng.choice(("delivered", "delivered", "read")))
        steps = [("j4", ctx.at(r["check_in"], 10))]
        if (r["check_out"] - r["check_in"]).days >= 2:
            steps.append(("j5", ctx.at(r["check_in"] + timedelta(days=1), 11)))
        steps.append(("j7", ctx.at(r["check_out"], 14)))
        for step, due in steps:
            if r["status"] == "NO_SHOW" and step != "j4":
                continue
            if due > now:
                _plan(ctx, rows, r, step, due, "planned")
            elif step == "j7" and r["channel"] == "EXPEDIA":
                _plan(ctx, rows, r, step, due, "skipped", skip="alias_expired")
            else:
                _plan(ctx, rows, r, step, due, rng.choice(("sent", "delivered", "delivered", "read")))
    return rows


def write_plans(ctx: Any, rows: list) -> list[int]:
    from psycopg2.extras import execute_values

    if not rows:
        return []
    got = execute_values(ctx.cur, """
        INSERT INTO gst_journey_plans (hotel_slug, reservation_id, step, due_at, valid_until, status, skip_reason,
            channel, contact_point_id, error_code, attempt, last_attempt_at) VALUES %s RETURNING id""", rows,
        page_size=1000, fetch=True)
    return [g["id"] for g in got]


def _reply(ctx: Any, plan_id: int, stay: dict, at_: datetime, outcome: str, cls: dict, summary: str,
           service_ref: str | None = None, hit: str | None = None) -> None:
    from psycopg2.extras import Json

    ctx.cur.execute(
        """INSERT INTO gst_journey_replies (hotel_slug, plan_id, reservation_id, classification, summary_en,
               deterministic_hit, outcome, service_ref, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (ctx.slug, plan_id, stay["id"], Json(cls), summary, hit, outcome, service_ref, at_))
    ctx.cur.execute("UPDATE gst_journey_plans SET status = 'replied' WHERE id = %s", (plan_id,))


def special_guests(ctx: Any, api: Any) -> dict:
    """The 04 spec 7.4 journey plants, each through the guests API."""
    a, special, sends = ctx.anchor, [], 0
    # 1. The repeat-AC guest (direct, own phone): J1/J4 read, the anchor
    #    morning's J5 answered "AC is not cooling", which opened the ticket.
    stay, req = ctx.stats.get("ops_repeat_stay"), ctx.stats.get("ops_j5_request")
    if stay and req:
        pt = capture(ctx, api, stay, source="console", phone=demo_phone(504), consents=("whatsapp_updates",))
        rows: list = []
        _plan(ctx, rows, stay, "j1", ctx.at(stay["check_in"] - timedelta(days=12), 11, 6), "read", channel="whatsapp",
              point=pt)
        _plan(ctx, rows, stay, "j4", ctx.at(stay["check_in"], 10), "read", channel="whatsapp", point=pt)
        _plan(ctx, rows, stay, "j5", ctx.at(a, 11), "read", channel="whatsapp", point=pt)
        ids = write_plans(ctx, rows)
        sends += len(ids)
        _reply(ctx, ids[-1], stay, ctx.at(a, 11, 20), "service_request",
               {"sentiment": "negative", "topic": "maintenance", "in_house": True},
               J5_TEXT.format(room=stay["room_number"]), service_ref=str(req["id"]), hit="not cooling")
        special.append(stay["pms_res_id"])
        plant(ctx, "guests.j5_negative_ticket", "ac-not-cooling", klass="ROW", module="guests",
              subjects=[{"pms_res_id": stay["pms_res_id"], "room": stay["room_number"]}],
              note="J5 reply 'AC is not cooling' opened a Service ticket (source whatsapp_journey).")
    # 2. A Booking guest who gave mobile, WhatsApp opt-in, marketing and
    #    transfer consent on the registration card, and later receives J8.
    reg = pick_stay(ctx, channel="BOOKING", check_out=(-25, -12), skip=set(special))
    if reg:
        pt = registration_card(ctx, api, reg, demo_phone(711))
        rows = []
        _plan(ctx, rows, reg, "j7", ctx.at(reg["check_out"], 14), "delivered", channel="whatsapp", point=pt)
        _plan(ctx, rows, reg, "j8", ctx.at(reg["check_out"] + timedelta(days=30), 10), "planned",
              channel="whatsapp", point=pt)
        sends += len(write_plans(ctx, rows))
        special.append(reg["pms_res_id"])
        plant(ctx, "guests.registration_card_consent", "booking-guest-j8", klass="ROW", module="guests",
              subjects=[{"pms_res_id": reg["pms_res_id"]}],
              note="Own mobile + WhatsApp + marketing + transfer consent from the registration card; J8 planned.")
    # 3. A Booking guest who typed a phone on the J1 page: a guest_stay
    #    point (this stay only) that gets J4 and J5, never J8.
    j1 = pick_stay(ctx, channel="BOOKING", in_house=True, skip=set(special))
    if j1:
        pt = capture(ctx, api, j1, source="j1_form", phone=demo_phone(712), consents=("whatsapp_updates",))
        rows = []
        _plan(ctx, rows, j1, "j4", ctx.at(j1["check_in"], 10), "read", channel="whatsapp", point=pt)
        if j1["check_in"] < a:
            _plan(ctx, rows, j1, "j5", ctx.at(j1["check_in"] + timedelta(days=1), 11), "delivered",
                  channel="whatsapp", point=pt)
        sends += len(write_plans(ctx, rows))
        special.append(j1["pms_res_id"])
        plant(ctx, "guests.j1_guest_stay", "booking-j1-phone", klass="ROW", module="guests",
              subjects=[{"pms_res_id": j1["pms_res_id"]}], note="A J1-page phone on an OTA stay: guest_stay, no J8.")
    sends += stop_and_131049(ctx, api, special)
    return {"special": special, "journey_sends": sends}


def stop_and_131049(ctx: Any, api: Any, special: list[str]) -> int:
    """One guest answers J7 with STOP (guests.api.opt_out suppresses the
    number and withdraws its messaging consents); one J3 offer hits Meta's
    131049 twice and is skipped (no marketing email consent to fall back on)."""
    sends = 0
    stop = pick_stay(ctx, channel="DIRECT_WEB", check_out=(-20, -5), skip=set(special))
    if stop:
        phone = demo_phone(713)
        pt = capture(ctx, api, stop, source="console", phone=phone, consents=("whatsapp_updates",))
        rows: list = []
        _plan(ctx, rows, stop, "j7", ctx.at(stop["check_out"], 14), "read", channel="whatsapp", point=pt)
        ids = write_plans(ctx, rows)
        sends += len(ids)
        with at(ctx, ctx.at(stop["check_out"], 14, 25)):
            _reply(ctx, ids[0], stop, clock.now(), "opt_out", {"intent": "stop"}, "STOP", hit="STOP")
            api.opt_out(ctx.cur, ctx.slug, kind="phone", value=phone, source="whatsapp")
        special.append(stop["pms_res_id"])
        plant(ctx, "guests.optout", "j7-stop", klass="ROW", module="guests",
              subjects=[{"pms_res_id": stop["pms_res_id"], "phone": phone}], note="STOP after J7: suppressed.")
    meta = pick_stay(ctx, channel="DIRECT_WEB", check_in=(2, 7), skip=set(special))
    if meta is None:  # small scales: a direct booking made for the plant
        ci = ctx.anchor + timedelta(days=3)
        meta = ops_stay(ctx, "meta_131049", channel="DIRECT_WEB", check_in=ci, nights=2, status="CONFIRMED",
                        guest=("Sofia Rossi", "IT", "en"), room=free_room(ctx, ci, ci + timedelta(days=2)))
    if meta:
        pt = capture(ctx, api, meta, source="console", phone=demo_phone(714),
                     consents=("whatsapp_updates", "marketing_whatsapp"))
        rows = []
        _plan(ctx, rows, meta, "j3", ctx.at(ctx.anchor - timedelta(days=1), 10), "skipped", channel="whatsapp",
              point=pt, skip="meta_131049", error="131049", attempt=2)
        sends += len(write_plans(ctx, rows))
        special.append(meta["pms_res_id"])
        plant(ctx, "guests.meta_131049", "j3-offer", klass="ROW", module="guests",
              subjects=[{"pms_res_id": meta["pms_res_id"]}], note="J3 blocked twice by Meta 131049, skipped.")
    return sends


FEEDBACK = ((5, "Friendly front desk, spotless room", None), (4, "Great location near the metro", "Breakfast queue"),
            (3, "Good value", "Slow lift in the evening"), (2, None, "Room smelled of smoke and nobody moved us"),
            (1, None, "AC broke twice and the shower drain was blocked"))


def j7_feedback(ctx: Any, rng: Any) -> int:
    """J7 answers at ratings 5 to 1. Every guest saw the same Google and
    Tripadvisor links; a rating of 2 or less files the guest_feedback
    follow-up through service.api.create_followup after the answer."""
    svc = ctx.module_api("service")
    ctx.cur.execute(
        """SELECT p.id, p.reservation_id, r.pms_res_id, r.check_out, r.guest_language, r.room_number
           FROM gst_journey_plans p JOIN reservations r ON r.id = p.reservation_id
           WHERE p.hotel_slug = %s AND p.step = 'j7' AND p.status IN ('sent', 'delivered', 'read')
             AND r.check_out BETWEEN %s AND %s ORDER BY r.check_out DESC, r.pms_res_id LIMIT %s""",
        (ctx.slug, ctx.anchor - timedelta(days=10), ctx.anchor - timedelta(days=1), len(FEEDBACK)))
    done = 0
    for (overall, well, improve), p in zip(FEEDBACK, ctx.cur.fetchall()):
        at_ = ctx.at(p["check_out"], 18, 30)
        ref = None
        if overall <= 2 and svc is not None:
            with at(ctx, at_):
                f = svc.create_followup(ctx.cur, ctx.slug, kind="guest_feedback", summary=f"J7 rating {overall}/5",
                                        guest_ref=p["pms_res_id"], owner_role="gm", due_at=at_ + timedelta(days=1),
                                        source="j7_feedback", language=p["guest_language"], room=p["room_number"])
            ref = str(f.get("id")) if f.get("id") else None
        ctx.cur.execute(
            """INSERT INTO gst_feedback (hotel_slug, reservation_id, plan_id, overall, went_well, improve, contact_ok,
                   language, followup_ref, submitted_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (ctx.slug, p["reservation_id"], p["id"], overall, well, improve, overall <= 2, "en", ref, at_))
        ctx.cur.execute("UPDATE gst_journey_plans SET status = 'done' WHERE id = %s", (p["id"],))
        done += 1
    if done:
        plant(ctx, "guests.j7_feedback", "ratings-1-to-5", klass="ROW", module="guests",
              subjects=[{"ratings": [f[0] for f in FEEDBACK][:done]}],
              note="J7 answers at every rating, identical review links; ratings <= 2 filed a GM follow-up.")
    return done


def demo_review_links(slug: str) -> dict[str, str]:
    """The hotel's Google and Tripadvisor review links, as an owner pastes
    them in Journeys > Settings. WHY: with none configured the J7 page
    showed no links at all, so the "every rating sees the same links" rule
    could not be shown (QA GS-6). Official hosts (the settings validators
    refuse anything else), a DEMO place id, derived from the slug so a
    re-seed keeps the same URLs."""
    return {"google_url": f"https://search.google.com/local/writereview?placeid=DEMO-{slug}",
            "tripadvisor_url": f"https://www.tripadvisor.ae/UserReviewEdit-DEMO-{slug}.html"}


def guest_review_links(ctx: Any) -> dict[str, str]:
    """Merge the demo review links into the guests settings through the
    module's own model (so the URL validators run) and the audited core
    writer. Every other guests setting keeps its value. The model comes
    through guests.api.demo_seed_door() (foundation never imports a module's
    internals); seed_guests has already checked the module is on."""
    from clefs.core.hotels import set_module_settings

    door = ctx.module_api("guests").demo_seed_door()
    cfg = door.load_settings(ctx.cur, ctx.slug)
    cfg = cfg.model_copy(update={"review_links": door.ReviewLinksIn(**demo_review_links(ctx.slug))})
    set_module_settings(ctx.cur, ctx.slug, "guests", cfg.model_dump(mode="json"), actor=_actor())
    return cfg.review_links.model_dump()


LANG_NAMES = {"en": "English", "ar": "Arabic", "de": "German", "fr": "French", "ru": "Russian", "hi": "Hindi",
              "zh": "Chinese", "it": "Italian", "es": "Spanish", "ur": "Urdu", "fa": "Persian", "tr": "Turkish"}


def _stay_type(r: dict) -> str:
    if (r.get("children") or 0) > 0:
        return "family"
    if r.get("segment") in ("CORPORATE", "MICE", "CREW"):
        return "business"
    return "couple" if (r.get("adults") or 1) >= 2 else "solo"


def guest_roster(ctx: Any) -> int:
    """The front desk's roster (guest_rooms) for every in-house room, as the
    GuestModal leaves it: name, stay type, dates, language. WHY: the room
    QR page personalizes only from this roster, and the demo had none, so
    /stay/<slug>/<room>?k=... greeted nobody (QA GS-4). No contact values:
    those go through record_own_contact, never the roster JSON."""
    from psycopg2.extras import Json

    ctx.cur.execute(
        """SELECT DISTINCT ON (room_number) room_number, guest_name, guest_language, segment, adults, children,
                  check_in, check_out
           FROM reservations WHERE hotel_slug = %s AND status = 'IN_HOUSE' AND room_number IS NOT NULL
           ORDER BY room_number, check_in DESC, id""", (ctx.slug,))
    rows = [dict(r) for r in ctx.cur.fetchall()]
    for r in rows:
        lang = (r.get("guest_language") or "en").split("-")[0].lower()
        data = {"roomNumber": r["room_number"],
                "guests": [{"name": r["guest_name"] or "Guest", "type": "primary"}],
                "stayType": _stay_type(r), "checkIn": r["check_in"].isoformat(), "checkOut": r["check_out"].isoformat(),
                "preferences": {"tags": [], "language": LANG_NAMES.get(lang, lang)}, "occasions": []}
        ctx.cur.execute(
            """INSERT INTO guest_rooms (hotel_slug, room_number, data, updated_at) VALUES (%s, %s, %s, %s)
               ON CONFLICT (hotel_slug, room_number)
               DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at""",
            (ctx.slug, r["room_number"], Json(data), ctx.now))
    return len(rows)


def guest_links(ctx: Any) -> dict:
    """The /g (J1 preferences) and /g/f (J7 feedback) tokens the journey
    executor mints when it sends those steps; the seed writes the sent plans
    directly, so it mints them too (QA GS-4: the demo had no guest link at
    all). Expiry follows 04 spec 3.2: prefs at check-in day 23:59, feedback
    at the plan's valid_until. -> {prefs, feedback} counts; one sample URL of
    each goes to ctx.credentials (a token is a secret: printed, never logged
    nor returned in the part's counts)."""
    from datetime import time

    from clefs.settings import get_settings

    mint = ctx.module_api("guests").demo_seed_door().mint_journey_token

    ctx.cur.execute(
        """SELECT p.id, p.step, p.reservation_id, p.valid_until, r.check_in, r.status AS res_status
           FROM gst_journey_plans p JOIN reservations r ON r.id = p.reservation_id
           WHERE p.hotel_slug = %s AND p.status IN ('sent', 'delivered', 'read')
             AND ((p.step = 'j1' AND r.check_in >= %s AND r.status IN ('CONFIRMED', 'IN_HOUSE'))
                  OR (p.step = 'j7' AND p.valid_until > %s))
           ORDER BY p.id""", (ctx.slug, ctx.anchor, ctx.now))
    counts, sample = {"prefs": 0, "feedback": 0}, {}
    base = get_settings().web_base_url.rstrip("/")
    for p in ctx.cur.fetchall():
        if p["step"] == "j1":
            raw = mint(ctx.cur, ctx.slug, p["reservation_id"], "prefs",
                       expires_at=datetime.combine(p["check_in"], time(23, 59), tzinfo=ctx.now.tzinfo),
                       ref={"plan_id": p["id"]})
            key, path = "prefs", f"/g/{raw}"
        else:
            raw = mint(ctx.cur, ctx.slug, p["reservation_id"], "feedback", expires_at=p["valid_until"],
                       ref={"plan_id": p["id"]})
            key, path = "feedback", f"/g/f/{raw}"
        counts[key] += 1
        sample.setdefault(key, base + path)
    if sample:
        ctx.credentials["guest_links"] = sample
    return counts


def seed_guests(ctx: Any) -> dict:
    api = ctx.module_api("guests")
    if api is None or not ctx.table_exists("gst_journey_plans") or not ctx.ids.get("reservations"):
        return {"journey_sends": 0}
    guest_review_links(ctx)
    rng = ctx.rng("ops:guests")
    out = special_guests(ctx, api)
    ids = write_plans(ctx, journey_rows(ctx, rng, set(out.pop("special"))))
    out["journey_sends"] = out.get("journey_sends", 0) + len(ids)
    out["j7_feedback"] = j7_feedback(ctx, rng)
    # What the hourly worker and the front desk would have done by now: the
    # stays snapshot (gst_stays, so /g pages and the journey view carry the
    # dates) and the roster the room QR page personalizes from.
    out["roster_rooms"] = guest_roster(ctx)
    linked = run_job(ctx, "guests.link_stays") or {}
    out["stays_linked"] = int(linked.get("pms_linked") or 0)
    links = guest_links(ctx)
    out["prefs_links"], out["feedback_links"] = links["prefs"], links["feedback"]
    return out


# ─── Inquiries ───
# Every inquiry enters through inquiries.api.create_inquiry with the clock
# frozen at its arrival (dedupe, SLA timer, extraction, the fixed ack), and
# the tasks intake queues (extraction, the ack action, the SLA check) run
# from job_queue at their run_at, as the worker would. What a person does
# next (the reply, a lost reason) and the quote and payment rows have no
# api door: they are written as the console leaves them, and then the
# registered jobs (inquiries.leak_match, inquiries.verify) decide the leak
# and the verified booking themselves.

INQ_TASKS = ("inquiries.sla_check", "inquiries.extract", "core.actions.execute")
# source, count (04 spec 7.4: WhatsApp 22 incl. 6 CTWA, email 12, web 14,
# Meta 5, Google 4 plus 1 duplicate delivery, phone 3), consent basis
INQ_MIX = (("whatsapp", 22, "pre_contract_notice"), ("email", 12, "pre_contract_notice"),
           ("web_form", 14, "express_web_form"), ("meta_leadgen", 5, "pre_contract_notice"),
           ("google_leadform", 4, "pre_contract_notice"), ("phone_desk", 3, "staff_logged"))
CTWA_COUNT = 6
# text, nights, rooms, segment (what the guests ask for, deterministic)
INQ_ASKS = (
    ("Do you have a double room for {n} nights from {ci}? Two adults.", 3, 1, "fit"),
    ("Price for a twin room {n} nights from {ci}, breakfast included?", 2, 1, "fit"),
    ("Family of 4, need the two-bedroom unit for {n} nights from {ci}.", 4, 1, "fit"),
    ("Corporate rate for 2 rooms, {n} nights from {ci}, invoice to our company.", 3, 2, "corporate"),
    ("We are 8 rooms for a wedding group, {n} nights from {ci}.", 2, 8, "group"),
    ("Monthly rate for one room from {ci}, about {n} nights?", 28, 1, "long_stay"),
)
LOST_MIX = (("PRICE_VS_COMP", 4), ("GUEST_UNRESPONSIVE", 5), ("NO_AVAILABILITY", 1), ("BOOKED_COMPETITOR", 2),
            ("DATES_CHANGED", 1))


def inq_drain(ctx: Any, until: datetime) -> int:
    """Run this hotel's queued inquiry tasks due by `until`, each at its
    run_at, oldest first (a task may queue another)."""
    ran = 0
    while ran < 500:
        ctx.cur.execute(
            """SELECT id, name, payload, run_at FROM job_queue WHERE hotel_slug = %s AND status = 'queued'
               AND name = ANY(%s) AND run_at <= %s ORDER BY run_at, id LIMIT 1""",
            (ctx.slug, list(INQ_TASKS), until))
        job = ctx.cur.fetchone()
        if job is None:
            return ran
        with at(ctx, job["run_at"]):
            result = run_task(ctx, job["name"], dict(job["payload"]))
            ctx.cur.execute("UPDATE job_queue SET status = 'done', attempts = attempts + 1, finished_at = %s, "
                            "result = %s WHERE id = %s", (clock.now(), json.dumps(result, default=str), job["id"]))
        ran += 1
    return ran


def inq_create(ctx: Any, api: Any, t: datetime, source: str, basis: str, text: str, *, name: str,
               phone: str | None = None, email: str | None = None, ci: date | None = None, co: date | None = None,
               rooms: int | None = None, company: str | None = None, language: str = "en") -> dict:
    with at(ctx, t):
        got = api.create_inquiry(
            ctx.cur, ctx.slug, source=source, text=text,
            contact=api.ContactIn(name=name, phone=phone, email=email, company=company, language=language),
            check_in=ci, check_out=co, rooms=rooms, consent_basis=basis, actor=None)
    inq_drain(ctx, t + timedelta(minutes=2))
    return got


def inq_respond(ctx: Any, inq_id: int, t: datetime, *, stage: str = "replied", lost: str | None = None,
                body: str = "Thank you for your message. Please find our best available rate below.") -> None:
    """The reservations agent answers (and, for a closed one, records why it
    was lost), as the inquiry drawer writes it."""
    from psycopg2.extras import Json

    cur = ctx.cur
    cur.execute("SELECT stage, created_at, channel_ref, source FROM inq_inquiries WHERE id = %s", (inq_id,))
    row = cur.fetchone()
    mins = int((t - row["created_at"]).total_seconds() // 60)
    cur.execute(
        """UPDATE inq_inquiries SET stage = %s, lost_reason = %s, first_response_at = COALESCE(first_response_at, %s),
               first_response_by = COALESCE(first_response_by, 'staff'),
               first_response_business_min = COALESCE(first_response_business_min, %s), stage_changed_at = %s,
               last_outbound_at = %s, last_activity_at = %s, updated_at = %s WHERE id = %s""",
        (stage, lost, t, mins, t, t, t, t, inq_id))
    cur.execute("INSERT INTO inq_messages (hotel_slug, inquiry_id, direction, channel, body, author_type, created_at) "
                "VALUES (%s, %s, 'out', %s, %s, 'staff', %s)", (ctx.slug, inq_id, row["source"], body, t))
    cur.execute("INSERT INTO inq_stage_events (hotel_slug, inquiry_id, from_stage, to_stage, at, actor_type, reason, "
                "data) VALUES (%s, %s, %s, %s, %s, 'user', %s, %s)",
                (ctx.slug, inq_id, row["stage"], stage, t, lost, Json({"demo": True})))


# Everyday senders; never a plant's name (plants are found by contact name).
INQ_NAMES = ("Lina Haddad", "Mark Evans", "Olga Sokolova", "Vikram Rao", "Leila Mansour", "Daniel Moore",
             "Sara Lindqvist", "Hamza Siddiqui", "Ivan Orlov", "Tariq Al Amiri", "Nadia Petrenko", "Arun Kapoor")


def regular_inquiries(ctx: Any, api: Any, rng: Any, skip: dict[str, int]) -> int:
    """The everyday volume: answered by the reservations team (mostly inside
    the SLA), then booked elsewhere, lost for a reason, or still open."""
    a, n = ctx.anchor, 0
    for source, count, basis in INQ_MIX:
        for i in range(count - skip.get(source, 0)):
            n += 1
            text, nights, rooms, _seg = rng.choice(INQ_ASKS)
            t = ctx.at(a - timedelta(days=rng.randint(3, 60)), rng.randint(9, 20), rng.randrange(60))
            ci = t.date() + timedelta(days=rng.randint(5, 45))
            name = f"{INQ_NAMES[n % len(INQ_NAMES)]}"
            local = name.split(" (")[0].lower().replace(" ", ".")
            got = inq_create(ctx, api, t, source, basis, text.format(n=nights, ci=ci.isoformat()), name=name,
                             phone=demo_phone(800 + n) if source in ("whatsapp", "phone_desk", "meta_leadgen")
                             else None,
                             email=f"{local}.{n}@example.com" if source not in ("whatsapp", "phone_desk") else None,
                             ci=ci, co=ci + timedelta(days=nights), rooms=rooms)
            if not got.get("id"):
                continue
            if source == "whatsapp" and i < CTWA_COUNT:  # arrived from a click-to-WhatsApp ad
                ctx.cur.execute("UPDATE inq_inquiries SET ctwa_clid = %s WHERE id = %s",
                                (f"demo-ctwa-{got['id']}", got["id"]))
            answered = t + timedelta(minutes=rng.choice((4, 7, 9, 12, 18, 26, 45)))
            closed = ci < a or rng.random() < 0.35
            inq_respond(ctx, got["id"], answered)
            if closed:
                inq_respond(ctx, got["id"], answered + timedelta(days=2), stage="lost",
                            lost=_pick(rng, LOST_MIX) if ci >= a else "GUEST_UNRESPONSIVE",
                            body="Following up on our offer: shall we hold the room for you?")
    return n


def unanswered(ctx: Any, api: Any) -> list[dict]:
    """Two inquiries from this afternoon nobody has answered (past the SLA,
    over 2 hours old at 21:00): a 30-night stay and a corporate booking in
    GITEX week (7-11 Dec)."""
    a = ctx.anchor
    gitex = date(a.year, 12, 7) if a < date(a.year, 12, 7) else date(a.year + 1, 12, 7)
    long_ci = a + timedelta(days=8)
    out = [
        inq_create(ctx, api, ctx.at(a, 16, 40), "email", "pre_contract_notice",
                   f"Hello, I need a room for 30 nights from {long_ci.isoformat()}, one person. Monthly rate?",
                   name="Stefan Novak", email="stefan.novak.demo@example.com", ci=long_ci,
                   co=long_ci + timedelta(days=30), rooms=1),
        inq_create(ctx, api, ctx.at(a, 17, 50), "email", "pre_contract_notice",
                   f"Corporate booking: 5 rooms for GITEX, {gitex.isoformat()} for 5 nights. Please send your "
                   "best corporate rate and invoice terms.", name="Meera Iyer",
                   email="meera.iyer.demo@example.com", company="Northwind Systems", ci=gitex,
                   co=gitex + timedelta(days=5), rooms=5),
    ]
    for contact in ("Stefan Novak", "Meera Iyer"):
        plant(ctx, "inq.unanswered", f"unanswered:{contact}", module="inquiries", subjects=[{"contact": contact}],
              note="Unanswered past SLA; the brief reads '2 unanswered > 2 h'.")
    return out


def demo_schedule(total: int, td: int, ci: date, *, td_note: dict, pct: int | None = None,
                  first: tuple[int, int] | None = None) -> list[dict]:
    """A deposit schedule in the shape inquiries' quote builder stores
    ({seq, purpose, label, due_on, amount_minor, includes_td_minor, td_note}):
    a pct deposit or the first night (amount, TD), then the balance due at
    arrival. WHY: the console and /q format every milestone's amount_minor,
    and an ad hoc [{"pct": 30}] 500'd the Quote tab (QA OWN-03). td_note is
    inquiries' TD_DEPOSIT_NOTE, from its api's demo_seed_door()."""
    if first is not None:
        dep, dep_td, label = first[0], first[1], "First night"
    else:
        dep, dep_td, label = round(total * (pct or 0) / 100), round(td * (pct or 0) / 100), f"{pct}% deposit"
    return [
        {"seq": 1, "purpose": "deposit", "label": label, "due_on": None, "amount_minor": dep,
         "includes_td_minor": dep_td, "td_note": td_note if dep_td else None,
         **({"cumulative_pct": str(pct)} if pct is not None else {})},
        {"seq": 2, "purpose": "balance", "label": "Balance", "due_on": ci.isoformat(), "amount_minor": total - dep,
         "includes_td_minor": td - dep_td, "td_note": None},
    ]


def demo_cancellation(days: int) -> dict:
    return {"en": f"Free cancellation until {days} days before arrival. Later cancellations and no-shows are "
                  "charged the first night."}


def demo_group_terms(ci: date, rooms: int) -> dict:
    """group_terms()'s shape: keep at least 90% of the block, rooming list
    and cut-off 7 days before arrival."""
    cutoff = (ci - timedelta(days=7)).isoformat()
    return {"cutoff_date": cutoff, "attrition_pct": "90", "min_rooms_after_attrition": -(-rooms * 9 // 10),
            "rooming_list_due": cutoff, "label": "Dubai norms UNVERIFIED"}


def group_quote(ctx: Any, api: Any) -> dict | None:
    """A group of 12 rooms x 3 nights, quoted 10% below BAR: the quote's send
    action waits for the owner (the dial proposes it, as request_send does)."""
    from clefs.core import actions, signals
    from clefs.core.tax import StayLine, compose_stay
    from psycopg2.extras import Json

    a = ctx.anchor
    ci, rooms, nights, bar = a + timedelta(days=18), 12, 3, 330
    t = ctx.at(a - timedelta(days=2), 10, 15)
    got = inq_create(ctx, api, t, "web_form", "express_web_form",
                     f"Group booking for a conference: 12 rooms, 3 nights from {ci.isoformat()}. Breakfast needed.",
                     name="Hessa Al Mansoori", email="hessa.demo@example.com", ci=ci,
                     co=ci + timedelta(days=nights), rooms=rooms, company="Gulf Events Co")
    if not got.get("id"):
        return None
    inq_respond(ctx, got["id"], t + timedelta(minutes=22), body="Thank you, we are preparing your group quote.")
    rate = int(bar * 100 * 0.9)
    lines = [{"stay_date": (ci + timedelta(days=k)).isoformat(), "room_type_code": "STD_KING", "rooms": rooms,
              "rate_minor": rate, "bar_minor": bar * 100} for k in range(nights)]
    tax = compose_stay(ctx.cur, ctx.slug, lines=[StayLine(date.fromisoformat(ln["stay_date"]), "STD_KING", rooms, rate)
                                                  for ln in lines])
    total = tax.room + tax.sc + tax.mf + tax.vat + tax.td
    valid = t + timedelta(days=7)
    schedule = demo_schedule(total, tax.td, ci, td_note=api.demo_seed_door().td_deposit_note, pct=30)
    with at(ctx, t + timedelta(hours=2)):
        ctx.cur.execute(
            """INSERT INTO inq_quotes (hotel_slug, inquiry_id, version, status, lines, room_minor, sc_minor, mf_minor,
                   vat_minor, td_minor, td_nights_charged, td_units, total_minor, currency, tax_convention, below_bar,
                   max_discount_pct, valid_until, deposit_schedule, cancellation_terms, group_terms, language,
                   pricing_trace, guardrails_snapshot, auto_send_eligible, auto_send_reasons, created_at)
               VALUES (%s, %s, 1, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'AED', %s, TRUE, 10, %s, %s, %s, %s,
                   'en', %s, %s, FALSE, %s, %s) RETURNING *""",
            (ctx.slug, got["id"], Json(lines), tax.room, tax.sc, tax.mf, tax.vat, tax.td, tax.td_nights_charged,
             tax.td_units, total, tax.convention, valid, Json(schedule),
             Json(demo_cancellation(21)), Json(demo_group_terms(ci, rooms)),
             Json({"bar_minor": bar * 100, "discount_pct": 10, "computed_at": clock.now().isoformat()}),
             Json({"max_discount_pct": 15}), ["below_bar", "group"], clock.now()))
        quote = dict(ctx.cur.fetchone())
        act = actions.create_action(
            ctx.cur, ctx.slug, type="inquiries.send_quote",
            title=f"Send quote {got['ref'].upper()}-v1: AED {total / 100:,.2f}",
            params={"inquiry_id": got["id"], "quote_id": quote["id"], "version": 1, "segment": "group",
                    "rooms": rooms, "nights": nights, "total_minor": total, "below_bar": True,
                    "auto_send_eligible": False, "reasons": ["below_bar", "group"]},
            expected_minor=total, currency="AED", owner_role="revenue", dedupe_key=f"inq.quote:{quote['id']}:demo",
            actor=actions.SYSTEM, via="system", expires_at=valid)
        if act.get("status") != "proposed":
            return quote
        ctx.cur.execute("UPDATE inq_quotes SET status = 'awaiting_approval', action_id = %s WHERE id = %s",
                        (act["id"], quote["id"]))
        signals.upsert_signal(ctx.cur, ctx.slug, signals.SignalIn(
            module="inquiries", kind="inq.quote_awaiting_approval",
            dedupe_key=f"inq.quote_awaiting_approval:{quote['id']}",
            title=f"Quote {got['ref'].upper()}-v1 waits for approval (AED {total / 100:,.0f})",
            summary="below_bar, group", cadence="weekly", impact_minor=total, currency="AED",
            impact_kind="revenue_upside", confidence=0.6, owner_role="gm", due_at=valid,
            subject_type="inquiry", subject_id=str(got["id"])))
    plant(ctx, "inq.quote_awaiting_approval", "group-12x3", module="inquiries",
          subjects=[{"contact": "Hessa Al Mansoori"}],
          note="Group 12 rooms x 3 nights at 10% below BAR: the send waits for approval.")
    return quote


def leak_and_decoys(ctx: Any, api: Any) -> None:
    """A web inquiry (A-17) whose guest booked the same dates on Booking.com
    four days later: the Booking-provided real phone (pms_ota) has the same
    hmac, so inquiries.leak_match finds it. Decoys: a same-name OTA stay
    booked 35 days after an older inquiry, the same name with another phone
    and other dates, and a real web email against a Booking alias email."""
    a = ctx.anchor
    ci = a + timedelta(days=5)
    phone = demo_phone(901)
    t = ctx.at(a - timedelta(days=17), 11, 20)
    got = inq_create(ctx, api, t, "web_form", "express_web_form",
                     f"Hi, price for a double room for 2 nights from {ci.isoformat()}?", name="Karim Nasser",
                     phone=phone, email="karim.nasser.demo@example.com", ci=ci, co=ci + timedelta(days=2), rooms=1)
    inq_respond(ctx, got["id"], t + timedelta(minutes=11))
    room = free_room(ctx, ci, ci + timedelta(days=2))
    ops_stay(ctx, "leak", channel="BOOKING", check_in=ci, nights=2, status="CONFIRMED",
             guest=("Karim Nasser", "LB", "ar"), room=room, rate=525, ota_res_id="5550901001",
             booked_at=ctx.at(a - timedelta(days=13), 22, 14),
             contacts=[{"kind": "phone", "value": phone, "provenance": "pms_ota"}])
    plant(ctx, "inq.leaked_to_ota", "web-to-booking", module="inquiries", subjects=[{"contact": "Karim Nasser"}],
          note="Web inquiry A-17, Booking reservation A-13 with the same phone (pms_ota): leaked, AED ~182.52.")
    # Decoy 1: same name, but the OTA stay was booked 35 days after an inquiry
    # that is outside the 30-day window.
    t1 = ctx.at(a - timedelta(days=40), 15, 5)
    ci1 = a + timedelta(days=12)
    d1 = inq_create(ctx, api, t1, "whatsapp", "pre_contract_notice", f"Room for 3 nights from {ci1.isoformat()}?",
                    name="Rania Aziz", phone=demo_phone(902), ci=ci1, co=ci1 + timedelta(days=3), rooms=1)
    inq_respond(ctx, d1["id"], t1 + timedelta(minutes=8))
    ops_stay(ctx, "decoy_35d", channel="BOOKING", check_in=ci1, nights=3, status="CONFIRMED",
             guest=("Rania Aziz", "JO", "ar"), room=free_room(ctx, ci1, ci1 + timedelta(days=3)),
             ota_res_id="5550902001", booked_at=t1 + timedelta(days=35),
             contacts=[{"kind": "phone", "value": demo_phone(902), "provenance": "pms_ota"}])
    # Decoy 2: same name, another phone, other dates (5 days apart).
    t2 = ctx.at(a - timedelta(days=12), 12, 40)
    ci2 = a + timedelta(days=20)
    d2 = inq_create(ctx, api, t2, "whatsapp", "pre_contract_notice", f"Twin room 2 nights from {ci2.isoformat()}",
                    name="Omar Haddad", phone=demo_phone(903), ci=ci2, co=ci2 + timedelta(days=2), rooms=1)
    inq_respond(ctx, d2["id"], t2 + timedelta(minutes=6))
    ops_stay(ctx, "decoy_phone", channel="AGODA", check_in=ci2 + timedelta(days=5), nights=2, status="CONFIRMED",
             guest=("Omar Haddad", "JO", "ar"), room=free_room(ctx, ci2 + timedelta(days=5),
                                                                      ci2 + timedelta(days=7)),
             ota_res_id="5550903001", booked_at=t2 + timedelta(days=2),
             contacts=[{"kind": "phone", "value": demo_phone(904), "provenance": "pms_ota"}])
    # Decoy 3: a real web email against the Booking alias email of another guest.
    t3 = ctx.at(a - timedelta(days=9), 9, 50)
    ci3 = a + timedelta(days=9)
    d3 = inq_create(ctx, api, t3, "web_form", "express_web_form", f"Double room 2 nights from {ci3.isoformat()}",
                    name="Julia Weber", email="julia.weber.demo@example.com", ci=ci3,
                    co=ci3 + timedelta(days=2), rooms=1)
    inq_respond(ctx, d3["id"], t3 + timedelta(minutes=14))
    ops_stay(ctx, "decoy_alias", channel="BOOKING", check_in=ci3, nights=2, status="CONFIRMED",
             guest=("Tom Becker", "NL", "en"), room=free_room(ctx, ci3, ci3 + timedelta(days=2)),
             ota_res_id="5550904001", booked_at=t3 + timedelta(days=1),
             contacts=[{"kind": "email", "value": "tbecker.559211@guest.booking.com", "provenance": "ota_alias"}])
    plant(ctx, "inq.leaked_to_ota", "decoys", klass="DECOY", module="inquiries",
          subjects=[{"contacts": ["Rania Aziz", "Omar Haddad", "Julia Weber"]}],
          note="Same name +35 d, same name other phone and dates, web email vs Booking alias: no leak.")


NGENIUS_FEE_PCT = "2.5"  # the demo account's configured fee (04 spec 7.4)


def verified_booking(ctx: Any, api: Any) -> None:
    """A web inquiry answered and quoted; the guest pays the first-night
    deposit through an N-Genius link. The webhook body is the fixture (as
    the provider sends it) and the link carries what fetch_status returned;
    the guest's DIRECT_WEB reservation then lands in the PMS, and the
    registered inquiries.verify job matches it (identity, name, dates) and
    records the verified outcome: revenue, commission avoided, fee."""
    from clefs.core.tax import StayLine, compose_stay
    from psycopg2.extras import Json

    a = ctx.anchor
    ci, nights, rate = a + timedelta(days=10), 2, 400
    email, name = "priya.menon.demo@example.com", "Priya Menon"
    t = ctx.at(a - timedelta(days=20), 14, 0)
    got = inq_create(ctx, api, t, "web_form", "express_web_form",
                     f"Hello, a king room for 2 nights from {ci.isoformat()} for two adults please.", name=name,
                     email=email, ci=ci, co=ci + timedelta(days=nights), rooms=1)
    inq_respond(ctx, got["id"], t + timedelta(minutes=9))
    lines = [StayLine(ci + timedelta(days=k), "STD_KING", 1, rate * 100) for k in range(nights)]
    tax = compose_stay(ctx.cur, ctx.slug, lines=lines)
    first = compose_stay(ctx.cur, ctx.slug, lines=lines[:1])
    deposit = first.room + first.sc + first.mf + first.vat + first.td
    total = tax.room + tax.sc + tax.mf + tax.vat + tax.td
    sent = t + timedelta(minutes=40)
    ctx.cur.execute(
        """INSERT INTO inq_quotes (hotel_slug, inquiry_id, version, status, lines, room_minor, sc_minor, mf_minor,
               vat_minor, td_minor, td_nights_charged, td_units, total_minor, currency, tax_convention, below_bar,
               valid_until, deposit_schedule, cancellation_terms, language, pricing_trace, guardrails_snapshot,
               auto_send_eligible, created_at, sent_at)
           VALUES (%s, %s, 1, 'sent', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'AED', %s, FALSE, %s, %s, %s, 'en', %s, %s,
               TRUE, %s, %s) RETURNING *""",
        (ctx.slug, got["id"], Json([{"stay_date": ln.stay_date.isoformat(), "room_type_code": "STD_KING", "rooms": 1,
                                      "rate_minor": ln.rate_minor} for ln in lines]),
         tax.room, tax.sc, tax.mf, tax.vat, tax.td, tax.td_nights_charged, tax.td_units, total, tax.convention,
         sent + timedelta(days=3), Json(demo_schedule(total, tax.td, ci, td_note=api.demo_seed_door().td_deposit_note,
                           first=(deposit, first.td))),
         Json(demo_cancellation(3)), Json({"bar_minor": rate * 100}), Json({}), sent, sent))
    quote = dict(ctx.cur.fetchone())
    quote_id = quote["id"]
    # A sent quote has its guest link, as quotes.deliver mints it on send, so
    # /q/<token> and the console's "guest has a link" badge work in the demo.
    api.demo_seed_door().mint_token(ctx.cur, quote)
    ctx.cur.execute(
        "INSERT INTO inq_channel_accounts (hotel_slug, kind, config, status, connected_at) VALUES (%s, 'pay_ngenius', "
        "%s, 'active', %s) ON CONFLICT (hotel_slug, kind) DO UPDATE SET config = EXCLUDED.config RETURNING id",
        (ctx.slug, Json({"fee_pct": NGENIUS_FEE_PCT, "currency": "AED", "test_mode": True,
                         "outlet_ref": "demo-outlet"}), t - timedelta(days=30)))
    account = ctx.cur.fetchone()["id"]
    paid_at = sent + timedelta(hours=19)
    fee = int((deposit * float(NGENIUS_FEE_PCT) / 100) + 0.5)
    ref = f"demo-ni-{got['ref']}"
    body = {"eventName": "PURCHASED", "order": {"reference": ref, "state": "PURCHASED",
                                                "amount": {"currencyCode": "AED", "value": deposit}}}
    ctx.cur.execute(
        """INSERT INTO inq_payment_links (hotel_slug, inquiry_id, quote_id, provider, provider_ref, url, amount_minor,
               currency, purpose, status, expires_at, paid_at, paid_amount_minor, fee_minor, fee_source,
               idempotency_key, provider_payload, created_at)
           VALUES (%s, %s, %s, 'ngenius', %s, %s, %s, 'AED', 'deposit', 'paid', %s, %s, %s, %s, 'settings', %s, %s, %s)
           RETURNING id""",
        (ctx.slug, got["id"], quote_id, ref, f"https://paypage.demo.invalid/{ref}", deposit, sent + timedelta(days=2),
         paid_at, deposit, fee, f"demo:{ref}", Json(body), sent))
    link_id = ctx.cur.fetchone()["id"]
    ctx.cur.execute(
        "INSERT INTO inq_payment_events (hotel_slug, account_id, provider, provider_event_id, payment_link_id, "
        "payload, processed_at, received_at) VALUES (%s, %s, 'ngenius', %s, %s, %s, %s, %s)",
        (ctx.slug, account, f"status:{ref}:PURCHASED", link_id, Json(body), paid_at + timedelta(minutes=1), paid_at))
    ctx.cur.execute("UPDATE inq_inquiries SET stage = 'payment_pending', stage_changed_at = %s WHERE id = %s",
                    (sent, got["id"]))
    stay = ops_stay(ctx, "verified", channel="DIRECT_WEB", check_in=ci, nights=nights, status="CONFIRMED",
                    guest=(name, "IN", "en"), room=free_room(ctx, ci, ci + timedelta(days=nights)), rate=rate,
                    booked_at=paid_at + timedelta(minutes=30), contacts=[{"kind": "email", "value": email}])
    with at(ctx, paid_at + timedelta(hours=1)):
        run_job(ctx, "inquiries.verify")
    plant(ctx, "inq.verified_direct", "ngenius-deposit", klass="ROW", module="inquiries",
          subjects=[{"contact": name, "pms_res_id": stay["pms_res_id"], "deposit_minor": deposit,
                     "fee_minor": fee}],
          note="N-Genius paid webhook (fixture) for the first-night deposit, DIRECT_WEB match, verified in September.")


def web_widget(ctx: Any) -> str:
    """The hotel's website inquiry form, connected as an owner would from
    Settings > Website form, so /w/inquiry/<slug>?k=<key> works right after
    the seed (QA GS-5: the form 404'd until someone opened that tab, which
    front desk cannot). The key is public by design (it sits in the hotel's
    page) and derived from the slug, so a re-seed keeps the same URL. ->
    the preview URL the CLI summary prints."""
    import hashlib

    from clefs.settings import get_settings
    from psycopg2.extras import Json

    key = "demo" + hashlib.sha256(f"clefs-demo-widget:{ctx.slug}".encode()).hexdigest()[:20]
    ctx.cur.execute(
        "INSERT INTO inq_channel_accounts (hotel_slug, kind, config, status, connected_at) VALUES (%s, 'web_widget', "
        "%s, 'active', %s) ON CONFLICT (hotel_slug, kind) DO UPDATE SET config = EXCLUDED.config, status = 'active'",
        (ctx.slug, Json({"widget_key": key, "allowed_origins": []}), ctx.at(ctx.anchor - timedelta(days=60), 9, 0)))
    return f"{get_settings().web_base_url.rstrip('/')}/w/inquiry/{ctx.slug}?k={key}"


def seed_inquiries(ctx: Any) -> dict:
    api = ctx.module_api("inquiries")
    if api is None or not ctx.table_exists("inq_inquiries"):
        return {"inquiries": 0}
    form_url = web_widget(ctx)
    rng = ctx.rng("ops:inquiries")
    # The plants use these channels' share of the 04 spec 7.4 counts.
    skip = {"email": 2, "web_form": 4, "whatsapp": 2, "google_leadform": 1}
    n = regular_inquiries(ctx, api, rng, skip)
    verified_booking(ctx, api)
    leak_and_decoys(ctx, api)
    group_quote(ctx, api)
    # Google's lead form retries a delivery: the same lead twice, one inquiry.
    t = ctx.at(ctx.anchor - timedelta(days=6), 13, 5)
    ci = ctx.anchor + timedelta(days=14)
    for k in range(2):
        dup = inq_create(ctx, api, t + timedelta(minutes=3 * k), "google_leadform", "pre_contract_notice",
                         f"Room for 2 adults, 4 nights from {ci.isoformat()}", name="Aigerim Bekova",
                         email="aigerim.bekova.demo@example.com", ci=ci, co=ci + timedelta(days=4), rooms=1)
    inq_respond(ctx, dup["id"], t + timedelta(minutes=16))
    plant(ctx, "inq.google_duplicate", "lead-retry", klass="ROW", module="inquiries",
          subjects=[{"contact": "Aigerim Bekova", "outcome": dup["outcome"]}],
          note="A duplicate Google delivery is one inquiry.")
    unanswered(ctx, api)
    with at(ctx, ctx.at(ctx.anchor, 5, 0)):
        run_job(ctx, "inquiries.leak_match")
    inq_drain(ctx, ctx.now)
    ctx.cur.execute("SELECT count(*) AS n FROM inq_inquiries WHERE hotel_slug = %s", (ctx.slug,))
    return {"inquiries": int(ctx.cur.fetchone()["n"]), "inquiries_regular": n, "inquiry_form": form_url}


# ─── Upsell ───

# In-room dining sessions over the last 30 days through upsell.api: a quote
# per session (the pairing offer is logged "shown"), some guests accept the
# pairing (a second quote), most confirm (the order and its F&B request),
# the rest leave the cart. Delivery and folio settlement are the console's
# status buttons (no api door): written as they leave the order, and the
# linked request is completed like the device flow completes it.

# main item, pairing target, meal
BASKETS = (("IRD-MN-03", "IRD-SD-01", "dinner"), ("IRD-MN-04", "IRD-SD-01", "lunch"),
           ("IRD-MN-01", "IRD-BV-02", "dinner"), ("IRD-MN-02", "IRD-SD-02", "dinner"),
           ("IRD-BRK-02", "IRD-BV-01", "breakfast"), ("IRD-MN-05", "IRD-BV-03", "dinner"),
           ("IRD-DS-01", "IRD-BV-03", "dinner"))
MEAL_HOURS = {"breakfast": (7, 9), "lunch": (12, 14), "dinner": (19, 22)}
UPSELL_DAYS, SESSIONS_PER_DAY = 30, (1, 3)
PAIR_ACCEPT, CONFIRM = 0.35, 0.78
UPSELL_SOURCES = ("web", "voice", "whatsapp")


def upsell_session(ctx: Any, api: Any, s: Svc | None, n: int, t: datetime, room: str, basket: tuple, rng: Any,
                   *, live: bool = False) -> str:
    """One guest session. -> "abandoned" | "ordered" | "delivered". A live
    session (tonight's order) always takes the pairing and confirms."""
    items = ctx.ids.get("menu_items") or {}
    main, pair, _meal = basket
    if main not in items:
        return "abandoned"
    ref, source = f"demo-ups-{n:04d}", UPSELL_SOURCES[n % len(UPSELL_SOURCES)]
    lines = [{"item_id": items[main], "qty": 1}]
    with at(ctx, t):
        q = api.quote_for_session(ctx.cur, ctx.slug, room=room, lines=lines, session_ref=ref, source=source)
    accepted = pair in items and (live or rng.random() < PAIR_ACCEPT)
    if accepted:
        lines = lines + [{"item_id": items[pair], "qty": 1}]
        with at(ctx, t + timedelta(minutes=1)):
            api.record_upsell_event(ctx.cur, ctx.slug, kind="pairing", state="accepted", item_id=items[pair],
                                    source_item_id=items[main], session_ref=ref, room=room, source=source)
            q = api.quote_for_session(ctx.cur, ctx.slug, room=room, lines=lines, session_ref=ref, source=source)
    if not live and rng.random() >= CONFIRM:
        return "abandoned"
    with at(ctx, t + timedelta(minutes=2)):
        order = api.confirm_order(ctx.cur, ctx.slug, quote_id=str(q["quote_id"]), room=room, source=source,
                                  session_ref=ref)
    if live:
        return "ordered"
    done = t + timedelta(minutes=rng.randint(22, 38))
    ctx.cur.execute(
        "UPDATE ups_orders SET status = 'delivered', status_changed_at = %s, updated_at = %s, paid_at = %s, "
        "payment = 'room_charge' WHERE id = %s", (done, done, done + timedelta(hours=10), order["order_id"]))
    if accepted:
        with at(ctx, done):
            api.record_upsell_event(ctx.cur, ctx.slug, kind="pairing", state="fulfilled", item_id=items[pair],
                                    source_item_id=items[main], session_ref=ref, room=room, source=source)
    if s is not None and order.get("request_id"):
        _transition(s, int(order["request_id"]), done - timedelta(minutes=20), "acknowledged",
                    s.staff_for("ird"))
        _transition(s, int(order["request_id"]), done, "completed", s.staff_for("ird"))
    return "delivered"


def upsell_episodes(ctx: Any, s: Svc | None) -> list[tuple[datetime, Any]]:
    """The sessions as (instant, fn). Service plays them inside its time
    ordered episodes: each order files a request through intake, whose rate
    limit and merge window count the room's requests since `now - 10 min`
    with no upper bound, so later history must not exist yet."""
    api = ctx.module_api("upsell")
    if api is None or not ctx.ids.get("menu_items") or not ctx.ids.get("reservations"):
        return []
    rng = ctx.rng("ops:upsell")
    nights = stays_by_day(ctx)
    state = ctx.stats.setdefault("ops_upsell", {"sessions": 0, "abandoned": 0, "ordered": 0, "delivered": 0})
    out: list[tuple[datetime, Any]] = []

    def run(n: int, t: datetime, room: str, basket: tuple, live: bool = False) -> None:
        state["sessions"] += 1
        state[upsell_session(ctx, api, s, n, t, room, basket, rng, live=live)] += 1

    n = 0
    for k in range(UPSELL_DAYS, 0, -1):
        d = ctx.anchor - timedelta(days=k)
        stays = sorted(nights.get(d) or [], key=lambda r: r["room_number"])
        for _ in range(rng.randint(*SESSIONS_PER_DAY) if stays else 0):
            basket = rng.choice(BASKETS)
            lo, hi = MEAL_HOURS[basket[2]]
            t = ctx.at(d, rng.randint(lo, hi), rng.randrange(60))
            n += 1
            out.append((t, lambda n=n, t=t, room=rng.choice(stays)["room_number"], b=basket: run(n, t, room, b)))
    # Tonight: one order on its way when the owner opens the console.
    stays = sorted(nights.get(ctx.anchor) or [], key=lambda r: r["room_number"])
    if stays:
        n += 1
        t = ctx.at(ctx.anchor, 20, 42)
        out.append((t, lambda n=n, t=t, room=stays[0]["room_number"]: run(n, t, room, BASKETS[0], live=True)))
    return out


def seed_upsell(ctx: Any) -> dict:
    """The sessions ran inside the service episodes; this records the
    funnel (or plays the sessions itself when Service is off)."""
    if "ops_upsell" not in ctx.stats:
        for _t, fn in sorted(upsell_episodes(ctx, None), key=lambda e: e[0]):
            fn()
    st = ctx.stats.get("ops_upsell")
    if not st or not st["sessions"]:
        return {"upsell_sessions": 0}
    plant(ctx, "upsell.funnel", "ird-30-days", klass="ROW", module="upsell",
          subjects=[{k: st[k] for k in ("sessions", "abandoned", "ordered", "delivered")}],
          note="Quote -> pairing -> order funnel over 30 days, one live order.")
    return {"upsell_sessions": st["sessions"], "upsell_orders": st["ordered"] + st["delivered"]}


# ─── Part ───


SECTIONS = ("service", "amenities", "reviews", "guests", "inquiries", "upsell")


def _replay(ctx: Any) -> ReplayAI:
    ai = ReplayAI()
    for lang, texts in ORIGINALS.items():
        for type_key, original in texts.items():
            ai.add("service.translate", original, DESCRIPTIONS[type_key])
    return ai


def settle_outbox(ctx: Any) -> int:
    """The messages the seeded history queued (inquiry acks, notices) went
    out back then: with NOTIFY_MODE log or capture their core.notify.deliver
    tasks run now at their run_at (nothing leaves the machine). With
    NOTIFY_MODE live they are retracted instead (notify.cancel_queued), so a
    seed never messages a synthetic contact and a worker started later
    never delivers a weeks-old ack."""
    from clefs import notify
    from clefs.settings import get_settings

    live = get_settings().NOTIFY_MODE == "live"
    ctx.cur.execute(
        "SELECT id, payload, run_at FROM job_queue WHERE hotel_slug = %s AND status = 'queued' AND name = %s "
        "AND run_at <= %s ORDER BY run_at, id", (ctx.slug, "core.notify.deliver", ctx.now))
    rows = ctx.cur.fetchall()
    for job in rows:
        oid = int(job["payload"].get("outbox_id") or 0)
        if live:
            notify.cancel_queued(ctx.cur, outbox_id=oid, hotel_slug=ctx.slug, reason="demo_seed_history")
            continue
        with at(ctx, job["run_at"]):
            result = run_task(ctx, "core.notify.deliver", dict(job["payload"]))
            ctx.cur.execute("UPDATE job_queue SET status = 'done', attempts = attempts + 1, finished_at = %s, "
                            "result = %s WHERE id = %s", (clock.now(), json.dumps(result, default=str), job["id"]))
    return len(rows)


def final_detectors(ctx: Any) -> None:
    """The anchor evening's hourly detectors, after every section wrote its
    rows (the J5 reply adds the second AC report the repeat rule needs)."""
    with at(ctx, ctx.now):
        run_job(ctx, "service.repeat_fault_detect")


def seed_part(ctx: Any) -> dict:
    """Run the sections in order (Service first: its time-ordered episodes
    also play the upsell orders, and later sections read the rows it made),
    then the anchor evening's detectors and the outbox. -> counts."""
    out: dict[str, Any] = {}
    fns = {"service": seed_service, "amenities": seed_amenities, "reviews": seed_reviews, "guests": seed_guests,
           "inquiries": seed_inquiries, "upsell": seed_upsell}
    with replay_ai(_replay(ctx)):
        for name in SECTIONS:
            fn = fns.get(name)
            if fn is not None:
                out.update(fn(ctx) or {})
        final_detectors(ctx)
        out["outbox_settled"] = settle_outbox(ctx)
    return out


# ─── Checks (demo-check) ───
# surfaced() resolves one manifest entry against the database: the signal
# (or row) a planted case must produce, or, for a DECOY, the one it must
# not. The tests and demo-check share it, so they can never disagree.


def _signal_keys(cur: Any, slug: str, kind: str) -> list[str]:
    cur.execute("SELECT dedupe_key FROM signals WHERE hotel_slug = %s AND kind = %s", (slug, kind))
    return [r["dedupe_key"] for r in cur.fetchall()]


def _item_id(cur: Any, slug: str, key: str) -> int | None:
    cur.execute("SELECT id FROM amn_items WHERE hotel_slug = %s AND key = %s", (slug, key))
    row = cur.fetchone()
    return int(row["id"]) if row else None


def surfaced(cur: Any, slug: str, p: dict) -> bool:
    """True when the planted case produced what it should (for a DECOY: the
    signal it must not produce exists, i.e. True is a failure)."""
    det, subj = p["detector"], (p.get("subjects") or [{}])[0]
    keys = _signal_keys(cur, slug, det) if "." in det else []
    if p.get("dedupe_key"):
        return p["dedupe_key"] in keys
    if det == "service.sla_breach_cluster":
        return any(k.startswith(f"{det}:{subj['business_date']}:") for k in keys)
    if det == "service.l1_incident":
        return _l1(cur, slug, subj)
    if det == "service.recovery_case":
        cur.execute("SELECT count(*) AS n FROM svc_recovery_cases c JOIN svc_recovery_actions a ON a.case_id = c.id "
                    "WHERE c.hotel_slug = %s AND c.room = %s AND c.guest_ref = %s AND a.status = 'given'",
                    (slug, subj["room"], subj["pms_res_id"]))
        return cur.fetchone()["n"] > 0
    if det.startswith("inq."):
        return _inquiries_surfaced(cur, slug, det, subj)
    if det == "upsell.funnel":
        ordered = _one(cur, "SELECT count(*) FROM ups_orders WHERE hotel_slug = %s", (slug,))
        accepted = _one(cur, "SELECT count(*) FROM ups_upsell_events WHERE hotel_slug = %s AND state = 'accepted'",
                        (slug,))
        return ordered >= subj["ordered"] + subj["delivered"] and accepted > 0 and bool(_one(
            cur, "SELECT count(*) FROM ups_orders WHERE hotel_slug = %s AND status = 'placed'", (slug,)))
    if det.startswith("reviews."):
        return _reviews_surfaced(cur, slug, det, subj)
    if det.startswith("guests."):
        return _guests_surfaced(cur, slug, det, subj)
    if det == "am.variance.spike":
        iid = _item_id(cur, slug, subj["item_key"])
        return iid is not None and f"{det}:{iid}" in keys
    return False


def _one(cur: Any, sql: str, params: tuple) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return next(iter(row.values())) if row else None


def _inquiries_surfaced(cur: Any, slug: str, det: str, subj: dict) -> bool:
    def iid(contact: str) -> Any:
        """Plants name inquiries by contact (refs are random per seed)."""
        return _one(cur, "SELECT min(id) FROM inq_inquiries WHERE hotel_slug = %s AND contact_name = %s",
                    (slug, contact))

    def outcome(i: Any) -> Any:
        return _one(cur, "SELECT outcome FROM inq_outcomes WHERE inquiry_id = %s", (i,))

    def signal(kind: str, i: Any) -> bool:
        return bool(_one(cur, "SELECT count(*) FROM signals WHERE hotel_slug = %s AND kind = %s AND (subject_id = %s "
                              "OR dedupe_key LIKE %s)", (slug, kind, str(i), f"%:{i}")))

    if det == "inq.leaked_to_ota" and "contacts" in subj:  # the decoys: any leak (or leak review) is a failure
        ids = [iid(c) for c in subj["contacts"]]
        return any(outcome(i) == "leaked_to_ota" or _one(
            cur, "SELECT count(*) FROM inq_match_reviews WHERE inquiry_id = %s AND kind = 'leak'", (i,)) for i in ids)
    i = iid(subj["contact"])
    if i is None:
        return False
    if det == "inq.leaked_to_ota":
        return outcome(i) == "leaked_to_ota" and signal(det, i)
    if det == "inq.verified_direct":
        return outcome(i) == "verified_direct"
    if det == "inq.google_duplicate":
        return _one(cur, "SELECT count(*) FROM inq_inquiries WHERE hotel_slug = %s AND source = 'google_leadform' "
                         "AND contact_name = (SELECT contact_name FROM inq_inquiries WHERE id = %s)", (slug, i)) == 1
    return signal(det, i)


def _reviews_surfaced(cur: Any, slug: str, det: str, subj: dict) -> bool:
    if det == "reviews.drafted_replies":
        return _one(cur, """SELECT count(*) FROM rev_reviews r WHERE r.hotel_slug = %s AND NOT r.has_owner_response
            AND r.posted_at >= now() - make_interval(days => %s) - interval '3 days'
            AND NOT EXISTS (SELECT 1 FROM rev_replies p WHERE p.review_id = r.id)""", (slug, subj["days"])) == 0
    rid = _one(cur, "SELECT id FROM rev_reviews WHERE hotel_slug = %s AND external_id = %s",
               (slug, subj["external_id"]))
    if rid is None:
        return False
    if det == "reviews.linked_request":
        return bool(_one(cur, """SELECT count(*) FROM rev_derived d
            JOIN staff_requests r ON r.id = ANY(d.linked_request_ids)
            WHERE d.review_id = %s AND r.room = %s""", (rid, subj["room"])))
    return bool(_one(cur, """SELECT count(*) FROM signals WHERE hotel_slug = %s AND kind = %s
        AND (subject_id = %s OR dedupe_key LIKE %s)""", (slug, det, str(rid), f"%:{rid}")))


def _guests_surfaced(cur: Any, slug: str, det: str, subj: dict) -> bool:
    res = "(SELECT id FROM reservations WHERE hotel_slug = %s AND pms_res_id = %s)"
    key = (slug, subj.get("pms_res_id"))
    if det == "guests.j5_negative_ticket":
        return bool(_one(cur, f"""SELECT count(*) FROM gst_journey_replies g JOIN staff_requests r
            ON r.id::text = g.service_ref WHERE g.hotel_slug = %s AND g.outcome = 'service_request'
            AND r.source = 'whatsapp_journey' AND r.room = %s AND g.reservation_id = {res}""",
            (slug, subj["room"], *key)))
    if det == "guests.registration_card_consent":
        return bool(_one(cur, f"""SELECT count(*) FROM gst_contact_points c JOIN gst_journey_plans p
            ON p.reservation_id = c.reservation_id AND p.step = 'j8' AND p.status = 'planned'
            WHERE c.hotel_slug = %s AND c.capture_source = 'registration_card' AND c.provenance = 'guest_direct'
            AND c.reservation_id = {res}""", (slug, *key)))
    if det == "guests.j1_guest_stay":
        return bool(_one(cur, f"""SELECT count(*) FROM gst_contact_points c WHERE c.hotel_slug = %s
            AND c.provenance = 'guest_stay' AND c.reservation_id = {res} AND NOT EXISTS (SELECT 1 FROM
            gst_journey_plans p WHERE p.reservation_id = c.reservation_id AND p.step = 'j8')""", (slug, *key)))
    if det == "guests.optout":
        return bool(_one(cur, f"""SELECT count(*) FROM gst_journey_replies g WHERE g.hotel_slug = %s
            AND g.outcome = 'opt_out' AND g.reservation_id = {res}""", (slug, *key))) and bool(
            _one(cur, "SELECT count(*) FROM gst_suppressions WHERE hotel_slug = %s", (slug,)))
    if det == "guests.meta_131049":
        return bool(_one(cur, f"""SELECT count(*) FROM gst_journey_plans WHERE hotel_slug = %s
            AND skip_reason = 'meta_131049' AND reservation_id = {res}""", (slug, *key)))
    if det == "guests.j7_feedback":
        cur.execute("SELECT overall, followup_ref FROM gst_feedback WHERE hotel_slug = %s", (slug,))
        rows = cur.fetchall()
        return ({r["overall"] for r in rows} >= set(subj["ratings"])
                and all(r["followup_ref"] for r in rows if r["overall"] <= 2))
    return False


def _l1(cur: Any, slug: str, subj: dict) -> bool:
    """The L1 request on that day in that room, its incident (open or closed
    as planted) and its service.l1_incident signal."""
    cur.execute(
        """SELECT r.id FROM staff_requests r JOIN svc_incidents i ON i.request_id = r.id
           WHERE r.hotel_slug = %s AND r.room = %s AND r.severity = 'L1' AND i.status = %s
             AND (to_timestamp(r."timestamp" / 1000.0) AT TIME ZONE 'Asia/Dubai')::date = %s""",
        (slug, subj["room"], "open" if subj["open"] else "closed", subj["day"]))
    ids = [r["id"] for r in cur.fetchall()]
    return any(f"service.l1_incident:{i}" in _signal_keys(cur, slug, "service.l1_incident") for i in ids)


def ops_checks(cur: Any, slug: str) -> list[tuple[str, bool, str]]:
    """Each planted ops case surfaces and no decoy fires (expected_plants.json
    entries of module service, amenities, reviews, guests, inquiries, upsell)."""
    import json
    import os

    from clefs.seed.demo_uae import MANIFEST_PATH

    if not os.path.exists(MANIFEST_PATH):
        return [("ops plants", False, "no expected_plants.json (run seed-demo first)")]
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        plants = [p for p in json.load(fh).get("plants", []) if p.get("module") in SECTIONS]
    if not plants:
        return [("ops plants", False, "the manifest has no ops plants (re-run seed-demo)")]
    out = []
    for p in plants:
        ok = surfaced(cur, slug, p)
        want = p["klass"] != "DECOY"
        out.append((f"ops {p['detector']} {p['key']}", ok == want, "surfaced" if ok else "not surfaced"))
    return out


CHECKS: list = [ops_checks]
