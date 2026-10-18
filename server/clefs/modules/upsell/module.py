"""
Menus, orders and upsell module manifest (MODULE is read by clefs.app.create_app).

Owned by lane F1 Upsell + Reviews from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

- menu.view: the Menu tab (menus, orders, funnel, metrics).
- menu.manage: edit outlets, items, pairings, import CSVs.
- menu.orders: move an order along (accepted, preparing, delivered, cancelled,
  refunded) and mark it paid; F&B staff and their shared devices hold it.
"""

from clefs.modules.upsell import jobs as _jobs  # noqa: F401  (stuck-order job, signal kind, PII registry)
from clefs.modules.upsell import metrics as _metrics  # noqa: F401  (registers upsell.* metrics)
from clefs.modules.upsell.router import router
from clefs.modules.upsell.router_public import router as public_router
from clefs.modules.upsell.settings import UpsellSettings
from clefs.registry import Module

MODULE = Module(
    name="upsell",
    routers=[router, public_router],
    permissions={
        "menu.view": frozenset({"owner", "gm", "dept_head", "front_desk", "finance", "revenue"}),
        "menu.manage": frozenset({"owner", "gm"}),
        "menu.orders": frozenset({"owner", "gm", "dept_head", "front_desk", "staff", "device"}),
    },
    settings_model=UpsellSettings,
)
