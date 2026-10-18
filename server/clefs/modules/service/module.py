"""
Service Control module manifest (MODULE is read by clefs.app.create_app).

Owned by lane A Service from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

Wiring (integrator, Wave 1):
- `config_api.ops_routers()` replaces router_public and router_device. Its
  public_api and devices_api serve the same paths with the same shapes plus
  more (the tracker WhatsApp opt-in, device actions, handover, SOS, recovery),
  so listing both would be a duplicate route (test_module_contracts).
- Importing config_api also imports service.whatsapp, which registers the
  WhatsApp fallback handler (priority 90).
- Importing service.jobs registers the 1.7 job set and the service.* metrics.
  Nothing else on the manifest path imports it, and jobs.load_all only reaches
  a module through this file, so without the import no service job would run.
"""

from clefs.auth.permissions import ALL_ROLES as ALL
from clefs.modules.service import config_api
from clefs.modules.service import jobs as _jobs  # noqa: F401  registers jobs + metrics
from clefs.modules.service.legacy import router as legacy_router
from clefs.modules.service.router import router
from clefs.registry import Module

MODULE = Module(
    name="service",
    routers=[router, legacy_router, *config_api.ops_routers()],
    permissions={
        "service.view": frozenset(ALL | {"device"}),
        "service.work": frozenset(ALL | {"device"}),
        "service.manage": frozenset({"owner", "gm", "dept_head", "front_desk"}),
    },
)
