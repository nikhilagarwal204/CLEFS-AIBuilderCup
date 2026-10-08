"""
Finance module manifest (MODULE is read by clefs.app.create_app).

Owned by lane P Finance. Permissions follow 04 spec 7.1:

- finance.view: the nav tab and module gate (revenue holds it for the pulse)
- finance.weekly.view: the weekly pulse
- finance.pl.view: every P&L route and section (monthly, trend, imports,
  mapping, anomalies, baseline)
- finance.act: decide_perm of finance.investigate_anomaly
- finance.import, finance.mapping.confirm, finance.publish, finance.connect:
  owner and finance (finance.connect also gates the Xero/QBO/Zoho OAuth start)
- finance.manage: the finance settings (ratio bands, market region)
"""

from clefs.modules.finance import actions as _actions  # noqa: F401  (finance.investigate_anomaly)
from clefs.modules.finance import imports as _imports  # noqa: F401  (PL_STATEMENT / TRIAL_BALANCE handlers)
from clefs.modules.finance import jobs as _jobs  # noqa: F401  (jobs and tasks)
from clefs.modules.finance import metrics as _metrics  # noqa: F401  (fin.* metrics for Ask)
from clefs.modules.finance import signals as _signals  # noqa: F401  (fin.* signal kinds)
from clefs.modules.finance.router import router
from clefs.modules.finance.settings import FinanceSettings
from clefs.registry import Module

_OWNER_FINANCE = frozenset({"owner", "finance"})
_PL = frozenset({"owner", "gm", "finance"})

MODULE = Module(
    name="finance",
    routers=[router],
    permissions={
        "finance.view": frozenset({"owner", "gm", "finance", "revenue"}),
        "finance.weekly.view": frozenset({"owner", "gm", "finance", "revenue"}),
        "finance.pl.view": _PL,
        "finance.act": _PL,
        "finance.import": _OWNER_FINANCE,
        "finance.mapping.confirm": _OWNER_FINANCE,
        "finance.publish": _OWNER_FINANCE,
        "finance.connect": _OWNER_FINANCE,
        "finance.manage": _OWNER_FINANCE,
    },
    settings_model=FinanceSettings,
)
