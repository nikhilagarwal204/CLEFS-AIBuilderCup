"""
Demand Radar module manifest (MODULE is read by clefs.app.create_app).

Owned by lane D Demand Radar from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

- demand.view: the Demand tab (calendar, events, holidays, markets, comp
  rates, BAR, guardrails, weather).
- demand.manage: manual events, ICS import, confirming AI discoveries,
  source-market shares, a manual source refresh, the comp set, rate
  settings, BAR entry and import, guardrails, comp-rate import and fetch.

Rates-side jobs (dr.comp_rates_fetch, dr.weather_fetch, dr.rates_retention)
and the COMP_RATES ingest handler register when router.py imports
comp_rates, weather and adapters.csv_rates. Engine jobs, signal kinds,
action types and metrics register from jobs.py; the engine's console routes
and the public demand-preview lead (/public/leads/demand-preview) are in
engine_routes.py.
"""

from clefs.modules.demand import events_jobs as _events_jobs  # noqa: F401  (event jobs, dr.event.changed, ICS handler)
from clefs.modules.demand import jobs as _engine_jobs  # noqa: F401  (engine jobs, signals, action types, metrics)
from clefs.modules.demand.engine_routes import public_router as engine_public_router
from clefs.modules.demand.engine_routes import router as engine_router
from clefs.modules.demand.router import router
from clefs.registry import Module

MODULE = Module(
    name="demand",
    routers=[router, engine_router, engine_public_router],
    permissions={
        "demand.view": frozenset({"owner", "gm", "revenue"}),
        "demand.manage": frozenset({"owner", "gm", "revenue"}),
    },
)
