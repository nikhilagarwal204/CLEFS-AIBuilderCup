"""
Module registry: the single, explicit list of product modules.

Why an explicit list instead of auto-discovery: the foundation ships a stub for
every module, so module agents only fill in their own directory and never touch
this file, app.py or main.py. Route order is deterministic (path precedence
matters in FastAPI) and a half-built module never loads by accident.

FROZEN after the foundation wave. Module agents leave `# FOUNDATION-REQUEST:`
notes in their own code instead of editing this file.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import APIRouter
from pydantic import BaseModel

# ─── Installed modules (order = router mount order) ───

INSTALLED_MODULES: tuple[str, ...] = (
    "service",
    "concierge",
    "profit_guard",
    "demand",
    "amenities",
    "upsell",
    "reviews",
    "guests",
    "brief",
    "ask",
    "trust",
    "voice",
    "visibility",
    "inquiries",
    "finance",
)

# ─── Migration number ranges (inclusive) ───
# "core" covers two lanes that share the clefs/migrations directory:
#   core-foundation 0001-0019 (0001-0009 F-server-core, 0010-0019 F-contracts)
#   core-domain     0020-0099 (stays / ingest / tax, built by the domain lane)
# "integrator" is for Wave 2 hotfixes and also lives in clefs/migrations.

MIGRATION_RANGES: dict[str, tuple[int, int]] = {
    "core": (1, 99),
    "service": (100, 199),
    "concierge": (200, 299),
    "profit_guard": (300, 399),
    "demand": (400, 499),
    "amenities": (500, 549),
    "upsell": (550, 599),
    "reviews": (600, 649),
    "guests": (650, 699),
    "brief": (700, 749),
    "ask": (750, 799),
    "trust": (800, 849),
    "voice": (850, 899),
    "visibility": (900, 949),
    "inquiries": (950, 999),
    "finance": (1000, 1049),
    "integrator": (1900, 1999),
}

CORE_SUBRANGES: dict[str, tuple[int, int]] = {
    "core-foundation": (1, 19),
    "core-domain": (20, 99),
}

# Modules whose migrations live in clefs/migrations rather than a module dir.
CORE_MIGRATION_OWNERS: tuple[str, ...] = ("core", "integrator")

# ─── Table prefixes ───
# New tables a module creates must start with its prefix. Legacy tables are
# grandfathered to the module that now owns them (they predate prefixes and
# their names are part of the production schema).

TABLE_PREFIXES: dict[str, str] = {
    "service": "svc_",
    "concierge": "cc_",
    "profit_guard": "pfg_",
    "demand": "dr_",
    "amenities": "amn_",
    "upsell": "ups_",
    "reviews": "rev_",
    "guests": "gst_",
    "brief": "brf_",
    "ask": "ask_",
    "trust": "trs_",
    "voice": "vc_",
    "visibility": "vis_",
    "inquiries": "inq_",
    "finance": "fin_",
}

GRANDFATHERED_TABLES: dict[str, frozenset[str]] = {
    "service": frozenset({"staff_requests", "staff_members", "staff_shifts", "pending_requests"}),
    "concierge": frozenset({"conversation_logs", "hotel_locations"}),
    "guests": frozenset({"guest_rooms"}),
}

# ─── Route prefixes (Canon 13) ───
# Console routes live under /hotels/{slug}/<segment>/... ; the public, device and
# webhook surfaces are shared namespaces a module may add under. Legacy paths are
# listed explicitly so the contract test can tell them apart from new routes.

ROUTE_SEGMENTS: dict[str, tuple[str, ...]] = {
    "service": ("service",),
    "concierge": ("concierge",),
    "profit_guard": ("profit", "profit-guard"),
    "demand": ("demand",),
    "amenities": ("amenities",),
    "upsell": ("menu", "upsell"),
    "reviews": ("reviews",),
    "guests": ("guests",),
    "brief": ("brief",),
    "ask": ("ask",),
    "trust": ("trust",),
    "voice": ("voice",),
    "visibility": ("visibility",),
    "inquiries": ("inquiries",),
    "finance": ("finance",),
}

SHARED_ROUTE_PREFIXES: tuple[str, ...] = ("/public/", "/device/", "/webhooks/", "/internal/")

# ─── Key prefixes (04 spec F9) ───
# Jobs, tasks, metrics, action types and signal kinds are keyed
# "<prefix>.<what>". A module may use its own name, any of its route segments
# ("profit.", "menu."), or a short prefix listed here; the 02/03/04 specs
# already name keys such as "inq.unanswered", "vis.parity_gap_google" and
# "pg.dispute", and renaming them would churn three specs. Each entry repeats
# the module's name so the map reads as the full list of non-segment
# prefixes. "tax." is shared on purpose: Profit Guard owns the Tourism Dirham
# ledger and detectors (td_ledger) next to core's tax package (Canon: core/tax).
#
# Keys only. Permissions never read this map: register_permissions accepts
# the module name and route segments alone, so every permission key keeps a
# full prefix ("inquiries.approve", never "inq.approve"; 04 spec 7.1).
# Enforced by tests/foundation/test_module_contracts.py.

KEY_PREFIXES: dict[str, tuple[str, ...]] = {
    "service": ("service.", "ops."),
    "concierge": ("concierge.",),
    "profit_guard": ("profit_guard.", "pg.", "tax."),
    "demand": ("demand.", "dr."),
    "amenities": ("amenities.", "am."),
    "upsell": ("upsell.",),
    "reviews": ("reviews.", "rv."),
    "guests": ("guests.",),
    "brief": ("brief.", "wa."),
    "ask": ("ask.",),
    "trust": ("trust.",),
    "voice": ("voice.",),
    "visibility": ("visibility.", "vis."),
    "inquiries": ("inquiries.", "inq."),
    "finance": ("finance.", "fin."),
}

LEGACY_ROUTE_PATHS: dict[str, frozenset[str]] = {
    "service": frozenset(
        {
            "/hotels/{slug}/requests",
            "/hotels/{slug}/requests/{request_id}",
            "/hotels/{slug}/pending",
            "/hotels/{slug}/staff",
            "/hotels/{slug}/staff/seed",
            "/hotels/{slug}/staff/{staff_id}",
            "/hotels/{slug}/staff/{staff_id}/status",
            "/hotels/{slug}/staff-analytics",
        }
    ),
    "concierge": frozenset(
        {
            "/hotels/{slug}/conversation-logs",
            "/hotels/{slug}/locations",
            "/hotels/{slug}/locations/{location_slug}",
        }
    ),
    "guests": frozenset(
        {
            "/hotels/{slug}/guests",
            "/hotels/{slug}/guests/seed",
            "/hotels/{slug}/guests/{room_number}",
        }
    ),
}


def route_prefixes(module: str) -> tuple[str, ...]:
    """Every path prefix a module's routes may start with."""
    own = tuple(f"/hotels/{{slug}}/{seg}" for seg in ROUTE_SEGMENTS[module])
    return own + SHARED_ROUTE_PREFIXES


# ─── Module manifest ───


@dataclass
class Module:
    name: str  # must equal the registry key
    routers: list[APIRouter] = field(default_factory=list)
    # permission key -> roles holding it. Role "device" grants it to paired devices.
    permissions: dict[str, frozenset[str]] = field(default_factory=dict)
    worker_services: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    on_startup: Callable[[], None] | None = None
    # Pydantic model with defaults for hotel_module_settings[<module>].settings.
    settings_model: type[BaseModel] | None = None


def _csv_env(name: str) -> set[str]:
    return {x.strip() for x in os.getenv(name, "").split(",") if x.strip()}


def enabled_modules(modules: list[str] | None = None) -> list[str]:
    """Modules to load, in registry order.

    `modules` (create_app argument) wins; otherwise CLEFS_ONLY_MODULES narrows
    the list (tests of one module load only that module) and
    CLEFS_DISABLED_MODULES removes entries (a prod kill switch).
    """
    if modules is not None:
        wanted = set(modules)
    else:
        only = _csv_env("CLEFS_ONLY_MODULES")
        wanted = only if only else set(INSTALLED_MODULES)
    unknown = wanted - set(INSTALLED_MODULES)
    if unknown:
        raise ValueError(f"unknown modules: {sorted(unknown)}")
    disabled = _csv_env("CLEFS_DISABLED_MODULES")
    return [m for m in INSTALLED_MODULES if m in wanted and m not in disabled]


def load_module(name: str) -> Module:
    """Import clefs.modules.<name>.module and return its MODULE manifest."""
    mod = importlib.import_module(f"clefs.modules.{name}.module")
    manifest = getattr(mod, "MODULE", None)
    if not isinstance(manifest, Module):
        raise TypeError(f"clefs.modules.{name}.module must export MODULE = Module(...)")
    if manifest.name != name:
        raise ValueError(f"module manifest name {manifest.name!r} != registry key {name!r}")
    return manifest
