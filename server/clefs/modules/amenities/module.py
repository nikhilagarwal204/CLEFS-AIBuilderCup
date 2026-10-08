"""
Amenities module manifest (MODULE is read by clefs.app.create_app).

Owned by lane E Amenities. Permission defaults follow foundation spec C; the
pseudo-role "device" grants a permission to paired shared devices.

- amenities.view: reports, catalog, benchmarks (analyst roles).
- amenities.manage: catalog, costs, locations, par, request lines, recompute.
- amenities.issue: log issuance and stock counts (the mobile quick entry);
  housekeeping staff and the floor devices they share.

Importing amenities.jobs registers the jobs, signal kinds, action types,
metrics, the ISSUANCE and SUPPLIER_INVOICE ingest commit handlers and the
PII entry. Nothing else on the manifest path imports it, and jobs.load_all
only reaches a module through this file.
"""

from clefs.modules.amenities import jobs as _jobs  # noqa: F401  registers jobs, signals, actions, metrics
from clefs.modules.amenities.router import router
from clefs.registry import Module

MODULE = Module(
    name="amenities",
    routers=[router],
    permissions={
        "amenities.view": frozenset({"owner", "gm", "finance", "dept_head"}),
        "amenities.manage": frozenset({"owner", "gm", "dept_head"}),
        "amenities.issue": frozenset({"owner", "gm", "dept_head", "staff", "device"}),
    },
)
