"""
Reviews module manifest (MODULE is read by clefs.app.create_app).

Owned by lane F1 Upsell + Reviews from Wave 1. Permission defaults follow foundation spec C;
the pseudo-role "device" grants a permission to paired shared devices.

- reviews.view: the Reviews tab (feed, themes, trends, drafts, competitors).
- reviews.manage: connect sources, import CSVs, edit/approve/post/copy
  replies, record auto-reply consent, change settings. It is also the
  decide permission of the reviews.reply_* action types.
"""

from clefs.modules.reviews import autonomy as _autonomy  # noqa: F401  (reply action types)
from clefs.modules.reviews import jobs as _jobs  # noqa: F401  (jobs, signal kinds, PII registry, email handler)
from clefs.modules.reviews import metrics as _metrics  # noqa: F401  (registers reviews.* metrics)
from clefs.modules.reviews.router import router
from clefs.modules.reviews.settings import ReviewsSettings
from clefs.registry import Module

MODULE = Module(
    name="reviews",
    routers=[router],
    permissions={
        "reviews.view": frozenset({"owner", "gm", "front_desk", "dept_head"}),
        "reviews.manage": frozenset({"owner", "gm"}),
    },
    settings_model=ReviewsSettings,
)
