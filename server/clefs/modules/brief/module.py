"""
Owner Brief module manifest (MODULE is read by clefs.app.create_app).

Owned by lane G Brief + Ask from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

- brief.view: read the brief, its runs, reports, settings and action timelines.
- brief.manage: send now and change the brief settings (time, recipients).

Importing the manifest registers the jobs (clefs.jobs.load_all imports it):
brief.daily / brief.weekly / brief.monthly (hotel-local, brief_time_local
for the daily), brief.escalate, brief.service_risk, brief.weekly_report,
brief.delivery_fallback (10 min) and wa.template_sync (daily); and the
WhatsApp channel, button handlers (brief:, act:) and text handler.
"""

from clefs.modules.brief import brief as _brief  # noqa: F401  (daily/weekly/monthly jobs, signal kinds)
from clefs.modules.brief import escalate as _escalate  # noqa: F401  (brief.escalate)
from clefs.modules.brief import metrics as _metrics  # noqa: F401  (brief.* metrics)
from clefs.modules.brief import producers as _producers  # noqa: F401  (brief.service_risk)
from clefs.modules.brief import weekly as _weekly  # noqa: F401  (brief.weekly_report)
from clefs.modules.brief import templates as _templates  # noqa: F401  (wa.template_sync, wa.template_alert)
from clefs.modules.brief import fallback as _fallback  # noqa: F401  (brief.delivery_fallback, status hook)
from clefs.modules.brief import magic_brief, whatsapp  # whatsapp: the channel, button and text handlers
from clefs.modules.brief.brief import BriefSettings
from clefs.modules.brief.router import router
from clefs.registry import Module

MODULE = Module(
    name="brief",
    routers=[router, whatsapp.router, magic_brief.router],
    permissions={
        "brief.view": frozenset({"owner", "gm", "finance", "revenue", "dept_head"}),
        "brief.manage": frozenset({"owner", "gm"}),
    },
    settings_model=BriefSettings,
)
