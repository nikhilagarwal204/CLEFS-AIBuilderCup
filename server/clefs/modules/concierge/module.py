"""
Guest Concierge module manifest (MODULE is read by clefs.app.create_app).

Owned by lane B Concierge + Voice from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

router_public (stay context, session minting, and the session tool, turn and
chat routes through turns.router) is listed after legacy_router: the same
order the routes had when legacy.py chained it. settings_model makes
GET/PUT /hotels/{slug}/module-settings/concierge validate against
ConciergeSettings (extra="allow" keeps keys the legacy stay route stores).
"""

from clefs.modules.concierge.constants import ConciergeSettings
from clefs.modules.concierge.legacy import router as legacy_router
from clefs.modules.concierge.router import router
from clefs.modules.concierge.router_public import router as public_router
from clefs.registry import Module

MODULE = Module(
    name="concierge",
    routers=[router, legacy_router, public_router],
    permissions={
        "concierge.view": frozenset({"owner", "gm", "front_desk"}),
        "concierge.manage": frozenset({"owner", "gm"}),
    },
    settings_model=ConciergeSettings,
)
