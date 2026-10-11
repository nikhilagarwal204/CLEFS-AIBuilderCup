"""
Tokyo demo part "concierge_live": the guest concierge is switched on for
the demo hotel, exactly as for the UAE one (demo_uae.concierge_live:
concierge.go_live_demo, for demo hotels only, after the property, the
approved knowledge base and the operations are in place).
"""

from __future__ import annotations

from clefs.seed.demo_uae.concierge_live import CHECKS, PART_ORDER, seed_part  # noqa: F401

__all__ = ["CHECKS", "PART_ORDER", "seed_part"]
