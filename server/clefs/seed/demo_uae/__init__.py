"""
The synthetic UAE demo (02 spec 9; 00-PLAN "Demo dataset").

    python -m clefs.cli seed-demo --hotel demo-deira-crest [--anchor 2026-09-27]

Deterministic: every value comes from random.Random(RNG_SEED) sub-streams
and the anchor date. The whole run happens under clock.frozen(anchor evening,
Dubai), so the core services the seed calls (stays, devices, tax) see the
anchor as "now" and never read the wall clock either. The CLI without
--anchor seeds live instead: as of the real clock on today's anchor, with
the anchor day warped into [00:00, as_of] (DemoCtx.warp), so nothing it
plants lies after the moment the demo is opened. Only secrets (password
hashes, device tokens) and database created_at defaults differ between runs.

Idempotent: a re-run deletes every row of the target hotel first (reset.py),
and only when that hotel is a demo hotel (hotels.status = 'demo'). Real
hotels are refused, never touched.

Parts. The seed is a list of parts run in order inside one transaction.
This package ships the core parts (core.py, core_catalog.py, core_stays.py).
Later parts (statements and plants, operations, growth modules) are sibling
modules that define

    PART_ORDER: int          # core parts use 10-40; later parts 50+
    def seed_part(ctx: DemoCtx) -> dict   # counts for the summary

and are discovered automatically, so adding a part never edits this file.
A part reads what earlier parts made from ctx.ids and appends planted cases
to ctx.plants (written to expected_plants.json by the CLI). It may also
define CHECKS = [fn(cur, slug) -> [(name, ok, detail)]] for demo-check.

ctx.ids after the core parts:
    rooms            {room_number: {room_number, floor, wing, room_type_code, bedrooms, extension, ...}}
    staff            {plain name: staff_members.id};  staff_departments {plain name: department key}
    users            {role: users.id} for owner, gm, finance, revenue, front_desk
    device_id        the paired front-desk tablet
    kb_entries       [cc_kb_entries.id]
    outlets          {key: ups_outlets.id};  menu_items {sku: ups_menu_items.id}
    amenity_items    {key: amn_items.id} (empty until amenities.api can seed, see core_catalog)
    comp_set         [dr_comp_set.comp_id]
    reservations     [{id, pms_res_id, channel, ota_res_id, status, check_in, check_out, room_number,
                       room_type, guest_name, country, language, collect_model, rate_plan, segment,
                       booked_at, cancelled_at, rates}] sorted by pms_res_id (booking order)

Use ctx.rng("<part>:<purpose>") for randomness, ctx.at(day, hour) for
hotel-local instants and ctx.now for the seed's "now" (the anchor evening,
or as_of on a live run); never the wall clock.
"""

from __future__ import annotations

import importlib
import pkgutil
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from clefs.core import clock

# ─── Constants ───

DEMO_SLUG = "demo-deira-crest"
RNG_SEED = 20261001
TZ = ZoneInfo("Asia/Dubai")
# The seed runs "at" 21:00 on the anchor day: after check-outs and most
# arrivals, before the 04:00 business-date roll, so business_date == anchor.
ANCHOR_HOUR = 21
HISTORY_DAYS = 120
FORWARD_DAYS = 125
# ─── Live "as of" (QA OWN-08, GS-3) ───
# A seed run at 13:42 used to plant the anchor evening (brief built 21:00,
# requests at 20:32, inquiries "received in 4 hours"). With as_of set, the
# anchor day is warped monotonically into [00:00, as_of]: up to WARP_KEEP_H
# nothing moves (the 04:00 business-date roll and the early jobs keep their
# order around it), the last WARP_RECENT_H hours before 21:00 shift as one
# block (SLA ages, "Ack in", live requests keep their exact minutes), and the
# day in between is compressed. Other days never move.
WARP_KEEP_H = 5
WARP_RECENT_H = 3
# Before this hour there is no room for the warp: seed as of last night.
WARP_MIN_HOUR = 9


def default_anchor() -> date:
    """Today in Dubai. The one wall-clock read, made by the CLI before the
    seed starts (tests always pass an explicit anchor)."""
    return clock.now().astimezone(TZ).date()


def live_anchor(wall: datetime | None = None) -> tuple[date, datetime | None]:
    """(anchor, as_of) for a seed that must look live at `wall` (default: the
    real clock). After 21:00 the anchor evening is already past: (today,
    None). Before WARP_MIN_HOUR: last night, (yesterday, None). Otherwise
    (today, wall floored to the minute), so nothing lands after the moment
    the presenter opens the console."""
    local = (wall or clock.now()).astimezone(TZ).replace(second=0, microsecond=0)
    if local.hour >= ANCHOR_HOUR:
        return local.date(), None
    if local.hour < WARP_MIN_HOUR:
        return local.date() - timedelta(days=1), None
    return local.date(), local


def check_as_of(anchor: date, as_of: datetime | None) -> datetime | None:
    """as_of in Dubai time, refused unless it lies on the anchor day between
    WARP_MIN_HOUR and the anchor evening (outside it the warp cannot keep
    the business date and the recent block intact)."""
    if as_of is None:
        return None
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    local = as_of.astimezone(TZ).replace(microsecond=0)
    lo = datetime.combine(anchor, time(WARP_MIN_HOUR), tzinfo=TZ)
    hi = datetime.combine(anchor, time(ANCHOR_HOUR), tzinfo=TZ)
    if not lo <= local <= hi:
        raise ValueError(f"as_of must be between {lo.isoformat()} and {hi.isoformat()}")
    return None if local == hi else local


# ─── Context ───


@dataclass
class DemoCtx:
    cur: Any
    slug: str
    anchor: date
    seed: int = RNG_SEED
    scale: float = 1.0
    ids: dict[str, Any] = field(default_factory=dict)
    plants: list[dict] = field(default_factory=list)
    credentials: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    created: bool = False
    # None: the deterministic anchor evening (tests, --anchor). Else the
    # live moment the anchor day is warped into (live_anchor, check_as_of).
    as_of: datetime | None = None

    def rng(self, stream: str) -> random.Random:
        """An independent stream per part and purpose. A str seed is hashed
        with SHA-512 (not PYTHONHASHSEED), so it is stable across processes,
        and adding draws to one stream never shifts another part's data."""
        return random.Random(f"{self.seed}:{stream}")

    @property
    def hotel(self) -> Any:
        from clefs.core.hotels import get_hotel

        return get_hotel(self.cur, self.slug)

    def at(self, d: date, hour: int = 12, minute: int = 0) -> datetime:
        """A hotel-local instant on day d (on the anchor day, warped to as_of)."""
        return self.warp(datetime.combine(d, time(hour, minute), tzinfo=TZ))

    def warp(self, t: datetime) -> datetime:
        """The written anchor-day instant t as it happens when the seed runs
        as of self.as_of (identity without as_of or on any other day). Order
        is kept; 21:00 maps to as_of; after 21:00 shifts with the recent block."""
        if self.as_of is None or t.astimezone(TZ).date() != self.anchor:
            return t
        evening = datetime.combine(self.anchor, time(ANCHOR_HOUR), tzinfo=TZ)
        keep = datetime.combine(self.anchor, time(WARP_KEEP_H), tzinfo=TZ)
        recent = evening - timedelta(hours=WARP_RECENT_H)
        if t <= keep:
            return t
        if t >= recent:
            return t + (self.as_of - evening)
        squeezed = (self.as_of - timedelta(hours=WARP_RECENT_H) - keep) / (recent - keep)
        return (keep + (t - keep) * squeezed).replace(microsecond=0)

    @property
    def now(self) -> datetime:
        return self.at(self.anchor, ANCHOR_HOUR)

    @property
    def history_start(self) -> date:
        return self.anchor - timedelta(days=HISTORY_DAYS - 1)

    @property
    def forward_end(self) -> date:
        return self.anchor + timedelta(days=FORWARD_DAYS)

    def module_api(self, module: str) -> Any | None:
        """A module's api.py, loaded by name (foundation code never imports a
        module statically) and only when it is enabled for this hotel."""
        from clefs.core.hotels import module_enabled
        from clefs.registry import enabled_modules

        if module not in enabled_modules() or not module_enabled(self.cur, self.slug, module):
            return None
        try:
            return importlib.import_module(f"clefs.modules.{module}.api")
        except ImportError:
            return None

    def table_exists(self, name: str) -> bool:
        self.cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{name}",))
        return bool(self.cur.fetchone()["ok"])


# ─── Parts ───

Part = tuple[int, str, Callable[[DemoCtx], dict]]


def discover_parts() -> list[Part]:
    """Every sibling module with PART_ORDER and seed_part, in order. Helper
    modules start with an underscore and are skipped."""
    out: list[Part] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        order, fn = getattr(mod, "PART_ORDER", None), getattr(mod, "seed_part", None)
        if isinstance(order, int) and callable(fn):
            out.append((order, info.name, fn))
    return sorted(out, key=lambda p: (p[0], p[1]))


# ─── Orchestrator ───


def seed(
    cur: Any,
    slug: str = DEMO_SLUG,
    *,
    anchor: date | None = None,
    scale: float = 1.0,
    parts: list[str] | None = None,
    seed: int = RNG_SEED,
    password: str | None = None,
    as_of: datetime | None = None,
) -> dict:
    """Build (or rebuild) the demo hotel `slug` in the caller's transaction.

    anchor: the last history day (default: today in Dubai). scale: a
    multiplier on reservation volume (tests use a small one; 1.0 is the
    spec's ~2,600 history reservations). parts: run only these part names
    (e.g. ["core"] for the property without stays). password: one password
    for every demo user (default: a random one per user, printed by the CLI).
    as_of: the live moment on the anchor day the seed runs at instead of the
    anchor evening (see live_anchor); with neither anchor nor as_of given,
    both come from live_anchor(), so a daytime run never plants the future.

    Returns {hotel, created, anchor, parts: {name: counts}, credentials,
    plants} (credentials are for the CLI to print; never log them)."""
    from clefs.seed.demo_uae import _reset

    if anchor is None and as_of is None:
        anchor, as_of = live_anchor()
    anchor = anchor or as_of.astimezone(TZ).date()
    as_of = check_as_of(anchor, as_of)
    known = discover_parts()
    unknown = sorted(set(parts or ()) - {name for _o, name, _f in known})
    if unknown:
        raise ValueError(f"unknown demo part(s): {', '.join(unknown)}")
    if parts is not None and "core" not in parts:
        # The hotel is wiped first, so every later part needs core's rooms,
        # staff and users rebuilt in the same run.
        raise ValueError("the core part always runs first: include core in --parts")
    ctx = DemoCtx(cur=cur, slug=slug, anchor=anchor, seed=seed, scale=scale, as_of=as_of)
    ctx.credentials["password"] = password
    prev = getattr(clock, "_frozen", None)
    clock.freeze(ctx.now.astimezone(ZoneInfo("UTC")))
    try:
        ctx.created = _reset.prepare_hotel(ctx)
        results: dict[str, dict] = {}
        for _order, name, fn in known:
            if parts is not None and name not in parts:
                continue
            results[name] = fn(ctx) or {}
    finally:
        if prev is not None:
            clock.freeze(prev)
        else:
            clock.unfreeze()
    return {
        "hotel": slug,
        "created": ctx.created,
        "anchor": anchor.isoformat(),
        # The seed's "now" (manifest() leaves it out: plants stay byte-stable).
        "as_of": ctx.now.isoformat(),
        "parts": results,
        "wipe": ctx.stats.get("wipe") or {},
        "credentials": ctx.credentials,
        "plants": ctx.plants,
    }


# ─── Manifest ───

MANIFEST_PATH = __path__[0] + "/expected_plants.json"


def manifest(result: dict) -> dict:
    """The planted-case manifest (02 spec 9): what each detector must find,
    with ids and expected amounts, as later parts appended them. No
    timestamps, sorted keys: the same seed and anchor give the same bytes."""
    return {
        "hotel": result["hotel"],
        "anchor": result["anchor"],
        "seed": RNG_SEED,
        "plants": sorted(result.get("plants") or [], key=lambda p: (str(p.get("detector")), str(p.get("key")))),
    }


def write_manifest(result: dict, path: str | None = None) -> str:
    import json

    target = path or MANIFEST_PATH
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(manifest(result), fh, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        fh.write("\n")
    return target
