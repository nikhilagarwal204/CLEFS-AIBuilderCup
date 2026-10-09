"""
Trust module manifest (MODULE is read by clefs.app.create_app).

Owned by lane H Trust + Marketing from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

Importing the job, signal and PII files here registers them in every process
that loads the manifest (the API through create_app, the worker through
jobs.load_all), which is why the imports exist even though nothing below
names them.
"""

from clefs.modules.trust import chain, dsr, pii_classes, retention, tenant_export  # noqa: F401  (registrants)
from clefs.modules.trust.evals import runs as eval_runs, watch as eval_watch  # noqa: F401  (registrants)
from clefs.modules.trust.evals.router import router as evals_router
from clefs.modules.trust.pilots import checkpoints as pilot_checkpoints, benchmarks  # noqa: F401  (registrants)
from clefs.modules.trust.pilots.router import router as pilots_router
from clefs.modules.trust.public import router as public_router
from clefs.modules.trust.router import router
from clefs.registry import Module

MODULE = Module(
    name="trust",
    routers=[router, evals_router, pilots_router, public_router],
    permissions={
        # Department heads (the duty manager) read the Trust pages too: the
        # deck's quality gate (22 tests, shadow week) is theirs to answer for
        # on shift. Everything they can change stays owner/GM below.
        "trust.view": frozenset({"owner", "gm", "dept_head"}),
        # Audit viewer, diff drawer and CSV export (03 spec 5.1: owner/GM).
        "trust.audit.view": frozenset({"owner", "gm"}),
        # The DSR queue: verifying a requester and running the executors.
        "trust.privacy.manage": frozenset({"owner", "gm"}),
        # Retention policies, consent wording and the tenant export (owner only).
        "trust.manage": frozenset({"owner"}),
        # Evals and the readiness gate (03 spec 5.4). Only the owner may
        # override a red gate (the override is audited).
        "trust.evals.view": frozenset({"owner", "gm", "dept_head"}),
        "trust.evals.manage": frozenset({"owner", "gm"}),
        "trust.readiness.override": frozenset({"owner"}),
        # Pilot scorecard and ROI report (03 spec 5.5): finance reads it.
        "trust.pilots.view": frozenset({"owner", "gm", "finance"}),
        "trust.pilots.manage": frozenset({"owner", "gm"}),
        # Benchmark cohorts are platform data: no hotel role, Clefs admins only.
        "trust.benchmarks.admin": frozenset(),
    },
    on_startup=pii_classes.register,
)
