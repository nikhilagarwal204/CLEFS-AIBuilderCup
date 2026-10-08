"""
The catalog Ask offers one viewer at one hotel: every registered metric the
viewer's role may see (clefs.core.metrics.list_metrics does the permission
filter), plus Ask's derived ratios (spec 7 RatioDef: ADR, occupancy, RevPAR)
computed server-side from registered metrics, minus any dimension that would
expose a single guest. This catalog is the ONLY source of the tool enums, so
the model can never name a metric, dimension or guest field outside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clefs.core import metrics
from clefs.modules.ask.constants import PII_DIMENSIONS

# ─── Derived ratios ───


@dataclass(frozen=True)
class Derived:
    key: str
    label: str
    description: str
    unit: str  # money | percent
    num: str  # a registered metric key
    den: str  # a registered metric key, or "capacity" (rooms x days in the bucket)
    scale: float = 1.0  # 100 for percentages
    currency: str | None = None
    synonyms: tuple[str, ...] = ()
    higher_is_better: bool | None = True


DERIVED: tuple[Derived, ...] = (
    Derived(
        key="ask.adr", label="ADR", unit="money", num="stays.room_revenue", den="stays.room_nights",
        description="Average daily rate: room revenue divided by occupied room nights, by stay date.",
        currency="hotel", synonyms=("average daily rate", "average rate", "net ADR"),
    ),
    Derived(
        key="ask.occupancy", label="Occupancy", unit="percent", num="stays.room_nights", den="capacity",
        description="Occupied room nights as a share of rooms available (hotel rooms x days), by stay date.",
        scale=100.0, synonyms=("occupancy rate", "occ", "how full"),
    ),
    Derived(
        key="ask.revpar", label="RevPAR", unit="money", num="stays.room_revenue", den="capacity",
        description="Room revenue per available room night (hotel rooms x days), by stay date.",
        currency="hotel", synonyms=("revenue per available room",),
    ),
)
_DERIVED_BY_KEY = {d.key: d for d in DERIVED}


def derived(key: str) -> Derived | None:
    return _DERIVED_BY_KEY.get(key)


# ─── Catalog ───


def _strip_pii(entry: dict) -> dict:
    entry = dict(entry)
    entry["dimensions"] = [d for d in entry.get("dimensions") or [] if d["key"] not in PII_DIMENSIONS]
    return entry


def _derived_entry(d: Derived, base: dict, hotel: Any) -> dict:
    return {
        "key": d.key,
        "label": d.label,
        "description": d.description,
        "unit": d.unit,
        "currency": hotel.currency if d.currency == "hotel" else None,
        "dimensions": list(base.get("dimensions") or []),
        "higher_is_better": d.higher_is_better,
        "synonyms": list(d.synonyms),
        "derived": True,
    }


def entries(ctx: Any) -> list[dict]:
    """[{key, label, description, unit, currency, dimensions[{key,label}],
    higher_is_better, synonyms, derived}] for this viewer, sorted by key."""
    visible = {m["key"]: _strip_pii({**m, "derived": False}) for m in metrics.list_metrics(ctx)}
    out = dict(visible)
    for d in DERIVED:
        if d.num not in visible or (d.den != "capacity" and d.den not in visible):
            continue  # the viewer cannot see an input, so cannot see the ratio
        if d.den == "capacity" and not getattr(ctx.hotel, "rooms_count", None):
            continue  # no room count: occupancy would divide by nothing
        out[d.key] = _derived_entry(d, visible[d.num], ctx.hotel)
    return [out[k] for k in sorted(out)]


def entry(ctx: Any, key: str) -> dict | None:
    return next((e for e in entries(ctx) if e["key"] == key), None)


def by_key(ctx: Any) -> dict[str, dict]:
    return {e["key"]: e for e in entries(ctx)}


def compact(entries_: list[dict]) -> list[dict]:
    """The short form the planner sees in its instructions and list_metrics."""
    return [
        {
            "key": e["key"],
            "label": e["label"],
            "unit": e["unit"] + (f" ({e['currency']})" if e.get("currency") else ""),
            "dimensions": [d["key"] for d in e["dimensions"]],
            "synonyms": e.get("synonyms") or [],
        }
        for e in entries_
    ]
