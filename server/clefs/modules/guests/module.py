"""
Guests module manifest (MODULE is read by clefs.app.create_app).

Owned by lane F2 Guests + Journeys. Permission defaults follow foundation
spec C and 04 spec 7.1; the pseudo-role "device" grants a permission to
paired shared devices (guests.contacts.capture adds a Front Office check).

Importing this file registers the identity part's job (guests.link_stays),
signal kind, metrics, PII entries and the WhatsApp STOP handler.

The journeys part (clefs.modules.guests.journeys) plugs in without editing
this file: when `journeys/manifest.py` exists, its ROUTERS, PERMISSIONS,
WORKER_SERVICES and on_startup are merged in below (all optional names).
"""

from __future__ import annotations

import importlib

from clefs.modules.guests import linking as _linking  # noqa: F401  (job + signal kind)
from clefs.modules.guests import optout as _optout
from clefs.modules.guests import registrations as _registrations  # noqa: F401  (metrics + PII)
from clefs.modules.guests.legacy import router as legacy_router
from clefs.modules.guests.router import router
from clefs.modules.guests.settings import GuestsSettings
from clefs.registry import Module

PERMISSIONS: dict[str, frozenset[str]] = {
    "guests.view": frozenset({"owner", "gm", "front_desk"}),
    "guests.manage": frozenset({"owner", "gm", "front_desk"}),
    # 04 spec 7.1: registration-card capture, devices included (the route
    # also requires a Front Office device, so housekeeping tablets cannot).
    "guests.contacts.capture": frozenset({"owner", "gm", "front_desk", "device"}),
    "guests.journeys.view": frozenset({"owner", "gm", "front_desk"}),
    "guests.journeys.manage": frozenset({"owner", "gm"}),
}


def _journeys_manifest():
    try:
        return importlib.import_module("clefs.modules.guests.journeys.manifest")
    except ModuleNotFoundError as exc:
        if exc.name not in ("clefs.modules.guests.journeys", "clefs.modules.guests.journeys.manifest"):
            raise
        return None


_journeys = _journeys_manifest()

MODULE = Module(
    name="guests",
    # The console router goes first: its multi-segment paths must win over
    # the legacy /hotels/{slug}/guests/{room_number} routes.
    routers=[router, *getattr(_journeys, "ROUTERS", []), _optout.router, legacy_router],
    permissions={**PERMISSIONS, **getattr(_journeys, "PERMISSIONS", {})},
    worker_services=list(getattr(_journeys, "WORKER_SERVICES", [])),
    on_startup=getattr(_journeys, "on_startup", None),
    settings_model=GuestsSettings,
)
