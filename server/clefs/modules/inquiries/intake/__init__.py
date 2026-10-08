"""Inquiry intake adapters (04 spec 4.3). Importing the package registers the
WhatsApp and email handlers; `core.ingest` is the pipeline they all feed."""

from clefs.modules.inquiries.intake import email as _email  # noqa: F401  (on_inbound_email)
from clefs.modules.inquiries.intake import whatsapp as _whatsapp  # noqa: F401  (on_whatsapp, priority 20)
from clefs.modules.inquiries.intake.core import Inbound, IntakeResult, ingest

__all__ = ["Inbound", "IntakeResult", "ingest"]
