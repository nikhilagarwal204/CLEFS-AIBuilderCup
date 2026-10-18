"""
Inquiry Guard module manifest (MODULE is read by clefs.app.create_app).

Owned by lane I Inquiry Guard. Permissions follow 04 spec 7.1; the pseudo-role
"device" is never granted (inquiries carry lead contacts).

- inquiries.view / inquiries.respond: the Inquiries tab and replying (contacts
  masked per the Trust matrix: revenue sees none).
- inquiries.quote: build quotes.
- inquiries.approve: decide_perm of send_quote, send_payment_link, follow_up.
- inquiries.manage: channels, payments, widget, settings.

Importing the registrants wires the WhatsApp (priority 20) and email
handlers, the ack action type, jobs, signal kinds and PII registrations.
"""

from clefs.modules.inquiries import ack as _ack  # noqa: F401  (inquiries.ack action type)
from clefs.modules.inquiries import intake as _intake  # noqa: F401  (WhatsApp + email handlers)
from clefs.modules.inquiries import jobs as _jobs  # noqa: F401  (tasks, signal kinds, PII)
from clefs.modules.inquiries import metrics as _metrics  # noqa: F401  (inq.* metrics)
from clefs.modules.inquiries.payments import routes as pay_routes
from clefs.modules.inquiries.router import router
from clefs.modules.inquiries.router_public import router as public_router
from clefs.modules.inquiries.router_webhooks import router as webhooks_router
from clefs.modules.inquiries.settings import InquirySettings
from clefs.registry import Module

_SALES = frozenset({"owner", "gm", "revenue", "front_desk"})

MODULE = Module(
    name="inquiries",
    # Quote-to-cash console routes go BEFORE the intake router: its
    # /{inquiry_id} would otherwise take /pipeline and /match-reviews.
    routers=[pay_routes.console, router, public_router, pay_routes.public, webhooks_router, pay_routes.webhooks],
    permissions={
        "inquiries.view": _SALES,
        "inquiries.respond": _SALES,
        "inquiries.quote": _SALES,
        "inquiries.approve": frozenset({"owner", "gm", "revenue"}),
        "inquiries.manage": frozenset({"owner", "gm"}),
    },
    settings_model=InquirySettings,
)
