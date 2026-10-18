"""
Visibility module manifest (MODULE is read by clefs.app.create_app).

Owned by lane V Visibility. Permission defaults follow 04 spec 7.1:
- visibility.view (owner, gm, revenue): the Visibility tab.
- visibility.act (owner, gm, revenue): propose the Google actions; the
  decide_perm of every visibility.* action type.
- visibility.manage (owner, gm): settings, the GBP location picker (and,
  through core.oauth, starting the google_business connection).

The imports below register signal kinds, action types, jobs and metrics, so
jobs.load_all() and create_app() both see them. Routers: the Google tabs
(router.py), the AI radar tab (ai_radar.router, /visibility/ai...) and the
public Google check (public_check.router, /public/visibility/google-check).
"""

from clefs.modules.visibility import action_types as _action_types  # noqa: F401  (visibility.* action types)
from clefs.modules.visibility import jobs as _jobs  # noqa: F401  (scheduled jobs, tasks, PII registry)
from clefs.modules.visibility import metrics as _metrics  # noqa: F401  (vis.* Google and AI metrics)
from clefs.modules.visibility import signals as _ai_signals  # noqa: F401  (vis.ai_* signal kinds)
from clefs.modules.visibility import vsignals as _vsignals  # noqa: F401  (vis.* Google signal kinds)
from clefs.modules.visibility.ai_radar import router as ai_router
from clefs.modules.visibility.public_check import router as public_router
from clefs.modules.visibility.router import router
from clefs.registry import Module

MODULE = Module(
    name="visibility",
    routers=[router, ai_router, public_router],
    permissions={
        "visibility.view": frozenset({"owner", "gm", "revenue"}),
        "visibility.act": frozenset({"owner", "gm", "revenue"}),
        "visibility.manage": frozenset({"owner", "gm"}),
    },
)
