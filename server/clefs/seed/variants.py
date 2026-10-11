"""
Which demo seed builds which demo hotel.

    jp   clefs.seed.demo_jp   Hotel Aozora Shinjuku, Tokyo (aozora-shinjuku): the
                              AI Builder Cup prototype's hotel and the default
    uae  clefs.seed.demo_uae  Al Rigga Crest Hotel, Dubai (demo-deira-crest): the
                              full product demo, every module seeded

A variant package has the same shape as demo_uae: TZ, DEMO_SLUG, seed(),
live_anchor(), write_manifest(), MANIFEST_PATH and a core module with the
deck cast (clefs.core.demo_access reads it through cast_for()).

How a slug finds its variant: an explicit choice (seed-demo --variant),
else the two demo hotels' own slugs, else an existing demo hotel's country
(a reseed keeps what was built), else the UAE seed, the one that exercises
every module.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

VARIANTS: dict[str, str] = {"jp": "clefs.seed.demo_jp", "uae": "clefs.seed.demo_uae"}
DEMO_HOTELS: dict[str, str] = {"aozora-shinjuku": "jp", "demo-deira-crest": "uae"}
COUNTRY_VARIANT: dict[str, str] = {"JP": "jp", "AE": "uae"}
DEFAULT_HOTEL = "aozora-shinjuku"
FALLBACK = "uae"


def key_for(slug: str, *, explicit: str | None = None, country: str | None = None) -> str:
    """The variant key for `slug` (see the module docstring)."""
    if explicit:
        if explicit not in VARIANTS:
            raise ValueError(f"unknown demo variant {explicit!r}: one of {', '.join(sorted(VARIANTS))}")
        return explicit
    if slug in DEMO_HOTELS:
        return DEMO_HOTELS[slug]
    return COUNTRY_VARIANT.get((country or "").strip().upper(), FALLBACK)


def package(key: str) -> ModuleType:
    return importlib.import_module(VARIANTS[key])


def for_slug(cur: Any, slug: str, *, explicit: str | None = None) -> ModuleType:
    """The variant package for `slug`, reading the hotel's country when the
    slug alone does not say (a demo hotel the CLI built under another slug)."""
    country = None
    if not explicit and slug not in DEMO_HOTELS and cur is not None:
        cur.execute("SELECT country FROM hotels WHERE slug = %s", (slug,))
        row = cur.fetchone()
        country = row["country"] if row else None
    return package(key_for(slug, explicit=explicit, country=country))


def cast_for(slug: str, country: str | None = None) -> ModuleType:
    """The deck cast (the variant's core module) of the demo hotel `slug`."""
    return importlib.import_module(f"{VARIANTS[key_for(slug, country=country)]}.core")
