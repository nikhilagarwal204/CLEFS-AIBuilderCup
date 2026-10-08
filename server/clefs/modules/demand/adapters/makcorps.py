"""
MakCorps comp-rate adapter: a stub (02 spec 4.2).

MakCorps resells OTA prices (200+ sources) under a paid contract (research
events-demand.md: Basic $350/month for 10k calls). The vendor carries the
collection risk, so its terms must be reviewed before a live adapter ships.
Until then:
- configured() is False without MAKCORPS_API_KEY, so the fetch run never
  builds the adapter;
- with a key, fetch() reports `stopped = "not_built"` and returns no rows
  (never a fabricated price).
The row source 'MAKCORPS' is reserved in dr_comp_rates for the live adapter.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.modules.demand.comp_rates import CompRate
from clefs.settings import env

SOURCE = "MAKCORPS"


def configured() -> bool:
    return bool((env("MAKCORPS_API_KEY", "") or "").strip())


class MakCorps:
    source = SOURCE

    def __init__(self, comps: list[dict]) -> None:
        self.comps = [c for c in comps if c.get("makcorps_id")]
        self.stopped: str | None = "not_built"
        self.report: dict = {"calls": 0, "stopped": self.stopped}

    def fetch(self, hotel: Any, comp_ids: list[str], dates: list[date], los: int = 1, adults: int = 2,
              currency: str = "AED") -> list[CompRate]:
        # The live call waits on a MakCorps contract review (terms, AED
        # support, rate limits); a stub returns nothing rather than guess.
        return []
