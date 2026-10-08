"""
Zoho Books connector: a stub (04 spec 6.3).

The research found no documented Reports module, so the plan is to build the
P&L from /chartofaccounts and /transactions. Zoho's data centres include
`.sa` (core.oauth exchanges codes only with an allowlisted accounts-server).
Until that is built every fetch raises NotConnected("coming next").
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.core.oauth import NotConnected
from clefs.modules.finance.connectors.common import Report

PROVIDER = "zoho"


class ZohoAdapter:
    provider = PROVIDER

    def fetch_pl(self, cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
        raise NotConnected("coming next", provider=PROVIDER)

    def fetch_tb(self, cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
        raise NotConnected("coming next", provider=PROVIDER)


ADAPTER = ZohoAdapter()


def fetch_pl(cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
    return ADAPTER.fetch_pl(cur, hotel_slug, month, currency)


def fetch_tb(cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
    return ADAPTER.fetch_tb(cur, hotel_slug, month, currency)
