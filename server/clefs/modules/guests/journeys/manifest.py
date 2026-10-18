"""
What guests/module.py merges in (see its docstring): the journeys routers,
and, by importing the parts, every registration the journeys own:

- stay-event consumer `guests.journeys` (planner)
- jobs: guests.journey_dispatch, guests.journey_plan_reconcile,
  guests.journey_contact_purge, guests.optout_monitor,
  guests.template_category_check, guests.wa_billing_check,
  guests.journey_feedback_expire
- action types guests.journey_j1_confirm ... guests.journey_j8_rebook,
  guests.checkout_recovery, guests.offer_accept
- WhatsApp handler guests.journey_replies (priority 10) and the
  on_outbound_status hook for purpose prefix "guests.journey"
- signal kinds and metrics (metrics.py)
"""

from __future__ import annotations

from clefs.modules.guests.journeys import dispatch as _dispatch  # noqa: F401  (guests.journey_dispatch)
from clefs.modules.guests.journeys import executor as _executor  # noqa: F401  (action types)
from clefs.modules.guests.journeys import feedback as _feedback  # noqa: F401
from clefs.modules.guests.journeys import gates as _gates  # noqa: F401
from clefs.modules.guests.journeys import replies as _replies  # noqa: F401  (priority-10 handler)
from clefs.modules.guests.journeys import status as _status  # noqa: F401  (on_outbound_status)
from clefs.modules.guests.journeys import maintenance as _maintenance  # noqa: F401  (jobs)
from clefs.modules.guests.journeys import metrics as _metrics  # noqa: F401  (metrics, signal kinds, PII)
from clefs.modules.guests.journeys import planner as _planner  # noqa: F401  (consumer + reconcile job)
from clefs.modules.guests.journeys import templates as _templates  # noqa: F401

from clefs.modules.guests.journeys.public import router as public_router
from clefs.modules.guests.journeys.router import router as console_router

ROUTERS: list = [console_router, public_router]
