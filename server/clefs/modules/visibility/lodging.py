"""
GBP Lodging facts vs the approved KB (04 spec 5.4, visibility.lodging_check).

The desired state is concierge.api.kb_facts(keyed_only=True) mapped fact by
fact onto an ALLOWLIST of Lodging fields; nothing outside it is ever written
(accessibility.* waits for a fact key). A difference becomes the action
visibility.lodging_sync (PATCH ?updateMask=<fields>, previous values in
reversal_handle); 24 h later getGoogleUpdated tells whether Google overrode
us, which is `not_verified` with evidence and the signal vis.lodging_overridden.

Field names follow the Lodging API reference (to be re-checked at build,
spec 7.7): policies.checkinTime / checkoutTime are TimeOfDay {hours, minutes}.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from clefs.core import clock
from clefs.modules.visibility import facts

LODGING_BASE = "https://mybusinesslodging.googleapis.com/v1"
RAW_TTL = timedelta(days=30)
SOURCE_URL = "https://business.google.com/"

# fact_key -> Lodging field paths (the allowlist, exactly the 5.4 table).
MAPPING: dict[str, tuple[str, ...]] = {
    "checkin_time": ("policies.checkinTime",),
    "checkout_time": ("policies.checkoutTime",),
    "parking": ("parking.parkingAvailable", "parking.freeParking"),
    "pool": ("pools.pool",),
    "breakfast": ("foodAndDrink.breakfastAvailable",),
    "pets": ("pets.petsAllowed",),
    "wifi": ("connectivity.wifiAvailable",),
}
ALLOWED_FIELDS = frozenset(f for fields in MAPPING.values() for f in fields)
TIME_FIELDS = frozenset({"policies.checkinTime", "policies.checkoutTime"})


# ─── Mapping ───


def desired_fields(truth: dict[str, str]) -> dict[str, Any]:
    """{lodging field path: value} for every mapped fact the KB states."""
    out: dict[str, Any] = {}
    for key, paths in MAPPING.items():
        v = truth.get(key)
        if v is None:
            continue
        if key in ("checkin_time", "checkout_time"):
            mins = facts.minutes(v)
            if mins is not None:
                out[paths[0]] = {"hours": mins // 60, "minutes": mins % 60}
        elif key == "parking":
            if v in ("none", "free", "paid"):
                out["parking.parkingAvailable"] = v != "none"
                out["parking.freeParking"] = v == "free"
        else:
            b = facts.as_bool(v)
            if b is not None:
                out[paths[0]] = b == "true"
    return out


def get_path(obj: dict, path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def nest(fields: dict[str, Any]) -> dict:
    """{"a.b": 1} -> {"a": {"b": 1}} (the PATCH body shape)."""
    out: dict = {}
    for path, v in fields.items():
        node = out
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    return out


def _time_str(v: Any) -> str | None:
    return facts.time_from_parts(v.get("hours", 0), v.get("minutes", 0)) if isinstance(v, dict) else None


def same(path: str, ours: Any, theirs: Any) -> bool:
    if path in TIME_FIELDS:
        return not facts.differs("checkin_time", _time_str(ours), _time_str(theirs)) and theirs is not None
    return theirs is not None and bool(ours) == bool(theirs)


def fact_for(path: str) -> str:
    return next(k for k, paths in MAPPING.items() if path in paths)


def parking_value(lodging: dict) -> str | None:
    avail = get_path(lodging, "parking.parkingAvailable")
    if avail is None:
        return None
    if not avail:
        return "none"
    return "free" if get_path(lodging, "parking.freeParking") else "paid"


def display(key: str, lodging: dict) -> str | None:
    """Google's value for a fact in the KB's vocabulary."""
    if key == "parking":
        return parking_value(lodging)
    v = get_path(lodging, MAPPING[key][0])
    if key in ("checkin_time", "checkout_time"):
        return _time_str(v)
    return facts.as_bool(v)


def diff(truth: dict[str, str], lodging: dict) -> list[dict]:
    """[{fact_key, field, ours, theirs, kind: missing|mismatch}] over the
    allowlist only. `ours`/`theirs` are Lodging values (what a PATCH writes)."""
    out = []
    for path, ours in desired_fields(truth).items():
        theirs = get_path(lodging, path)
        if same(path, ours, theirs):
            continue
        out.append({"fact_key": fact_for(path), "field": path, "ours": ours, "theirs": theirs,
                    "kind": "missing" if theirs is None else "mismatch"})
    return out


# ─── API calls ───


def fetch(cur: Any, hotel_slug: str, location: str) -> tuple[dict, dict]:
    """(lodging, google_updated) where google_updated is {lodging, diffMask}."""
    from clefs.modules.visibility import gbp

    lodging = gbp.call(cur, hotel_slug, "GET", f"{LODGING_BASE}/{location}/lodging", params={"readMask": "*"})
    updated = gbp.call(cur, hotel_slug, "GET", f"{LODGING_BASE}/{location}/lodging:getGoogleUpdated",
                       params={"readMask": "*"})
    return lodging, updated


def overridden_fields(updated: dict, fields: dict[str, Any]) -> list[dict]:
    """Our fields that Google changed: named in diffMask and holding a value
    different from ours."""
    mask = {m.strip() for m in str(updated.get("diffMask") or "").split(",") if m.strip()}
    g = updated.get("lodging") or {}
    out = []
    for path, ours in fields.items():
        if path in mask or any(path.startswith(m + ".") for m in mask):
            theirs = get_path(g, path)
            if not same(path, ours, theirs):
                out.append({"field": path, "ours": ours, "google": theirs})
    return out


# ─── Daily check ───


def run_check(cur: Any, hotel: Any) -> dict:
    """visibility.lodging_check (daily 06:15): snapshot both views, diff the
    allowlist against the KB and propose visibility.lodging_sync for a new
    difference (deduped on the diff itself, so an unchanged diff is one
    action, not one a day)."""
    from clefs.core import actions
    from clefs.db import Json
    from clefs.modules.visibility import gbp

    ok, st = gbp.ready(cur, hotel.slug)
    if not ok:
        return {"skipped": st["status"]}
    truth = facts.kb_truth(cur, hotel.slug)
    lodging, updated = fetch(cur, hotel.slug, st["location"])
    d = diff(truth, lodging)
    now = clock.now()
    cur.execute("INSERT INTO vis_lodging_snapshots (hotel_slug, fetched_at, kind, payload, diff_vs_kb, expires_at) "
                "VALUES (%s, %s, 'lodging', %s, %s, %s), (%s, %s, 'google_updated', %s, NULL, %s)",
                (hotel.slug, now, Json(lodging), Json(d), now + RAW_TTL, hotel.slug, now, Json(updated),
                 now + RAW_TTL))
    if not d:
        return {"diff": 0}
    fields = {x["field"]: x["ours"] for x in d}
    previous = {x["field"]: x["theirs"] for x in d}
    digest = hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]
    act = actions.create_action(
        cur, hotel.slug, type="visibility.lodging_sync",
        title=f"Update {len(d)} Google hotel fact(s) to match your approved answers",
        params={"location": st["location"], "fields": fields, "previous": previous,
                "facts": sorted({x["fact_key"] for x in d})},
        dedupe_key=f"visibility.lodging_sync:{digest}", owner_role="gm",
    )
    return {"diff": len(d), "action_id": act.get("id")}


def propose(cur: Any, hotel: Any, *, actor: Any = None) -> dict:
    """POST /visibility/actions/lodging-sync: run the check now."""
    return run_check(cur, hotel)


# ─── Executor and verifier ───


def execute(ctx: Any) -> Any:
    from clefs.core.actions import ExecResult
    from clefs.modules.visibility import gbp

    fields = {k: v for k, v in (ctx.params.get("fields") or {}).items() if k in ALLOWED_FIELDS}
    if not fields:
        return ExecResult(ok=False, error="nothing to update")
    ok, st = gbp.ready(ctx.cur, ctx.hotel_slug)
    if not ok:
        return ExecResult(ok=False, error=f"GBP {st['status']}")
    location = ctx.params.get("location") or st["location"]
    current = gbp.call(ctx.cur, ctx.hotel_slug, "GET", f"{LODGING_BASE}/{location}/lodging",
                       params={"readMask": "*"})
    previous = {f: get_path(current, f) for f in fields}
    gbp.call(ctx.cur, ctx.hotel_slug, "PATCH", f"{LODGING_BASE}/{location}/lodging",
             params={"updateMask": ",".join(sorted(fields))},
             json={"name": f"{location}/lodging", **nest(fields)})
    return ExecResult(ok=True, data={"location": location, "fields": fields, "previous": previous},
                      reversal_handle={"kind": "gbp_lodging_patch", "location": location, "previous": previous})


def verify(ctx: Any) -> Any:
    """24 h later: getGoogleUpdated must not show Google overriding any field
    we wrote. An override is not_verified with the evidence and a signal."""
    from clefs.core.actions import VerifyResult
    from clefs.modules.visibility import gbp, vsignals

    data = ctx.action.get("execution_result") or {}
    fields = data.get("fields") or ctx.params.get("fields") or {}
    location = data.get("location") or ctx.params.get("location")
    ok, st = gbp.ready(ctx.cur, ctx.hotel_slug)
    if not ok or not location:
        return VerifyResult(status="pending", evidence=[{"kind": "row", "label": "GBP", "value": st["status"]}])
    updated = gbp.call(ctx.cur, ctx.hotel_slug, "GET", f"{LODGING_BASE}/{location}/lodging:getGoogleUpdated",
                       params={"readMask": "*"})
    over = overridden_fields(updated, fields)
    if over:
        vsignals.lodging_overridden(ctx.cur, ctx.hotel_slug, over, action_id=ctx.action_id)
        return VerifyResult(status="not_verified", attribution="caused", evidence=[
            {"kind": "row", "label": "Google overrode", "value": over},
            {"kind": "quote", "label": "diffMask", "value": updated.get("diffMask")}])
    return VerifyResult(status="verified", attribution="caused",
                        evidence=[{"kind": "row", "label": "Fields kept", "value": sorted(fields)}])


# ─── Fact errors (visibility.api.open_fact_errors) ───


def lodging_fact_errors(cur: Any, hotel_slug: str) -> list[dict]:
    """Mismatches in the latest lodging snapshot, in the KB's vocabulary
    (a field Google simply lacks is not an error, only a sync candidate)."""
    cur.execute("SELECT payload, diff_vs_kb, fetched_at FROM vis_lodging_snapshots WHERE hotel_slug = %s "
                "AND kind = 'lodging' ORDER BY fetched_at DESC, id DESC LIMIT 1", (hotel_slug,))
    r = cur.fetchone()
    if not r or not r["diff_vs_kb"]:
        return []
    truth = facts.kb_truth(cur, hotel_slug)
    keys = sorted({x["fact_key"] for x in r["diff_vs_kb"] if x.get("kind") == "mismatch"})
    return [{"fact_key": k, "ours": truth.get(k), "theirs": display(k, r["payload"] or {}),
             "source_url": SOURCE_URL, "seen_at": r["fetched_at"].isoformat(), "source": "google_business_profile"}
            for k in keys]
