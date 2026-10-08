"""
Permissions, not raw roles.

Routes check permissions (`require_perm("profit.view")`); roles only appear in
the permission -> roles tables. The foundation registers core.* here; each
module registers its own through Module.permissions when app.py loads it, and
/auth/me returns the resolved list so the client never duplicates the matrix.

The pseudo-role "device" in a permission's role set grants it to paired devices.
A clefs_admin user passes every check (deps.py audits that bypass).
"""

from __future__ import annotations

import threading

from clefs.registry import ROUTE_SEGMENTS

ROLES: tuple[str, ...] = ("owner", "gm", "finance", "revenue", "dept_head", "front_desk", "staff")
DEVICE_ROLE = "device"
ALL_ROLES = frozenset(ROLES)
MANAGERS = frozenset({"owner", "gm"})
# Everyone who reads the console's numbers (not front_desk / staff).
ANALYSTS = frozenset({"owner", "gm", "finance", "revenue", "dept_head"})

CORE_PERMISSIONS: dict[str, frozenset[str]] = {
    "core.settings.view": ANALYSTS,
    "core.settings.manage": MANAGERS,
    "core.team.manage": MANAGERS,
    "core.signals.view": ANALYSTS,
    "core.actions.decide": MANAGERS,
    "core.devices.manage": MANAGERS,
    # Devices upload too (lost-and-found and maintenance photos from shift tablets).
    "core.files.upload": ALL_ROLES | {DEVICE_ROLE},
    # Contracts lane (CONTRACTS.md Part 2). The autonomy dial: everyone who
    # reads signals sees it, managers move it, and only an owner may let the
    # machine act on its own (auto levels, accepting a promotion).
    "core.autonomy.view": ANALYSTS,
    "core.autonomy.manage": MANAGERS,
    "core.autonomy.raise_auto": frozenset({"owner"}),
    "core.metrics.view": ANALYSTS,
    # Uploaded files hold OTA statements, P&Ls and guest PII: not for devices,
    # front desk or staff (uploaders still read their own files).
    "core.files.view": ANALYSTS,
    # Core domain (Wave 1 integration). Reservations, occupancy and matches
    # are read by every analyst role; only managers correct occupancy or
    # confirm a reservation match (the stays route tests pin finance out).
    "core.stays.view": ANALYSTS,
    "core.stays.manage": MANAGERS,
    # PMS and OTA-statement uploads: the people who hold those files.
    # Reads stay on core.settings.view.
    "core.ingest.manage": MANAGERS | {"finance", "revenue"},
}

# Magic-link sessions (WhatsApp/email) may only do what their purpose needs.
# Full console access needs a password login.
MAGIC_PURPOSE_PREFIXES: dict[str, tuple[str, ...]] = {
    "brief": ("brief.", "core.signals.view"),
    "actions": ("core.actions.",),
}

# What an anonymous visitor to a legacy_open_access hotel effectively had before
# auth existed: the whole staff dashboard (requests, staff, guests, locations).
LEGACY_OPEN_PERMISSIONS = frozenset(
    {
        "service.view",
        "service.manage",
        "concierge.view",
        "concierge.manage",
        "guests.view",
        "guests.manage",
    }
)

_lock = threading.Lock()
_registry: dict[str, frozenset[str]] = dict(CORE_PERMISSIONS)
_owners: dict[str, str] = {k: "core" for k in CORE_PERMISSIONS}


def permission_prefixes(module: str) -> tuple[str, ...]:
    """A module's permission keys start with its name or one of its route
    segments ("profit.view" for profit_guard, "menu.manage" for upsell)."""
    names = (module,) + ROUTE_SEGMENTS.get(module, ())
    return tuple(dict.fromkeys(f"{n}." for n in names))


def register_permissions(perms: dict[str, frozenset[str] | set[str]], *, module: str) -> None:
    prefixes = permission_prefixes(module)
    allowed_roles = ALL_ROLES | {DEVICE_ROLE}
    with _lock:
        for key, roles in perms.items():
            if not key.startswith(prefixes):
                raise ValueError(f"permission {key!r} of module {module!r} must start with one of {prefixes}")
            bad = set(roles) - allowed_roles
            if bad:
                raise ValueError(f"permission {key!r}: unknown roles {sorted(bad)}")
            owner = _owners.get(key)
            if owner is not None and owner != module:
                raise ValueError(f"permission {key!r} already registered by {owner!r}")
            _registry[key] = frozenset(roles)
            _owners[key] = module


def reset() -> None:
    """Back to core-only (create_app calls this before loading modules)."""
    with _lock:
        _registry.clear()
        _registry.update(CORE_PERMISSIONS)
        _owners.clear()
        _owners.update({k: "core" for k in CORE_PERMISSIONS})


def all_permissions() -> frozenset[str]:
    with _lock:
        return frozenset(_registry)


def permissions_for_role(role: str | None) -> frozenset[str]:
    if not role:
        return frozenset()
    with _lock:
        return frozenset(k for k, roles in _registry.items() if role in roles)


def device_permissions() -> frozenset[str]:
    return permissions_for_role(DEVICE_ROLE)


def legacy_open_permissions() -> frozenset[str]:
    return LEGACY_OPEN_PERMISSIONS


def restrict_for_magic(perms: frozenset[str], purposes: list[str] | tuple[str, ...]) -> frozenset[str]:
    prefixes: tuple[str, ...] = ()
    for p in purposes:
        prefixes += MAGIC_PURPOSE_PREFIXES.get(p, ())
    if not prefixes:
        return frozenset()
    return frozenset(k for k in perms if k.startswith(prefixes))


def owner_of(key: str) -> str | None:
    with _lock:
        return _owners.get(key)
