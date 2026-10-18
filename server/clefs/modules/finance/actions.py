"""
Finance action types (04 spec 6.8).

finance.investigate_anomaly is a human task: someone looks into a P&L
anomaly (a labour spike, a line that jumped, revenue booked net). It has no
executor and no verifier, and never counts toward Clefs-verified profit: an
investigation is not a measured outcome. Its ceiling is approve, since there
is nothing to automate.
"""

from __future__ import annotations

from clefs.core import actions
from clefs.core.actions import ActionType

actions.register_action_type(ActionType(
    key="finance.investigate_anomaly",
    module="finance",
    label="Investigate a P&L anomaly",
    default_owner_role="gm",
    default_level="approve",
    max_level="approve",
    decide_perm="finance.act",
    counts_toward_verified_profit=False,
    description="Look into a P&L anomaly flagged when the month was published.",
))
