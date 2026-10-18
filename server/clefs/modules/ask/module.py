"""
Ask Clefs module manifest (MODULE is read by clefs.app.create_app).

Owned by lane G Brief + Ask. Importing whatsapp registers the inbound text
handler (clefs.notify.on_whatsapp); importing this file registers the
question log as personal data (a question can name a person even though
Ask refuses to answer it).
"""

from clefs.core import privacy
from clefs.modules.ask import whatsapp  # noqa: F401 - registers the WhatsApp Q&A handler
from clefs.modules.ask.router import router
from clefs.registry import Module

# ─── Personal data ───
# Questions are free text; a year covers the owner's own look-back (and the
# eval review of answers marked wrong), then the row goes.
privacy.register_pii(
    "ask", "ask_logs", subject_columns={"question": "free_text"}, ts_column="created_at", retention_days=365,
    strategy="delete",
)

MODULE = Module(
    name="ask",
    routers=[router],
    permissions={
        "ask.view": frozenset({"owner", "gm", "finance", "revenue", "dept_head"}),
        # Review everyone's questions and the answers marked wrong.
        "ask.manage": frozenset({"owner", "gm"}),
    },
)
