"""
Profit Guard module manifest (MODULE is read by clefs.app.create_app).

Owned by lane C Profit Guard from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

Import order matters (core/ingest keeps one handler per kind, and the two
commit hooks wrap whatever handler is live when they install):
  1. imports.py registers the raw commit handlers for the Profit Guard kinds,
     so a statement committed from the console, an email forward or the
     public audit always lands in this module's tables;
  2. detectors/ wraps them with the detect-on-commit hook and registers the
     detector jobs and signal kinds;
  3. jobs.py wraps them again with the auto-verify hook and registers the
     pg.* action types, metrics and the ledger/tax jobs;
  4. public_audit.py registers pg.public_audit_purge and the /public routes.
"""

from clefs.modules.profit_guard import imports as _imports  # noqa: F401  (registers the commit handlers)
from clefs.modules.profit_guard import detectors as _detectors  # noqa: F401  (detector jobs + commit hook)
from clefs.modules.profit_guard import jobs as _pg_jobs  # noqa: F401  (actions, metrics, jobs, verify hook)
from clefs.modules.profit_guard import disputes, economics, ledger, public_audit, tax_checks
from clefs.modules.profit_guard.detectors.router import router as detectors_router
from clefs.modules.profit_guard.router import router
from clefs.modules.profit_guard.router_data import router as data_router
from clefs.registry import Module

MODULE = Module(
    name="profit_guard",
    routers=[
        router,
        data_router,
        detectors_router,
        ledger.router,
        disputes.router,
        tax_checks.router,
        economics.router,
        public_audit.router,
    ],
    permissions={
        "profit.view": frozenset({"owner", "gm", "finance"}),
        "profit.manage": frozenset({"owner", "gm", "finance"}),
        # Approving a dispute packet commits the hotel to a claim with the
        # OTA, so it is narrower than profit.manage (02 spec 3.6).
        "profit.approve_disputes": frozenset({"owner", "finance"}),
    },
)
