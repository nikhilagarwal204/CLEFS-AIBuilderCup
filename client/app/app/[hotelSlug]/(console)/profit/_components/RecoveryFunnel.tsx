"use client";

/* RecoveryFunnel: where the money found so far stands, as one stacked bar
   by status (Found -> Accepted -> Disputed -> Recovered, with the
   dead ends after). Amounts, not counts, size the bar, because one AED
   8,000 payout shortfall matters more than twelve AED 20 rounding lines.
   Colours come from the kit's categorical palette; the legend rows carry
   the numbers so colour is never the only cue. */

import { CHART_DONE, CHART_NEW, CHART_PROGRESS, CHART_QUIET } from "../../../../charts";
import { DIVERGING_NEG, SERIES_COLORS } from "../../../../../ui";
import { STATUS_LABEL, STATUS_ORDER } from "../lib";
import type { FindingStatus, MoneyBucket } from "../types";
import { Money } from "./bits";

const COLOR: Record<FindingStatus, string> = {
  DETECTED: CHART_NEW,
  ACCEPTED: SERIES_COLORS[3],
  DISPUTED: CHART_PROGRESS,
  RECOVERED: CHART_DONE,
  REJECTED: DIVERGING_NEG,
  UNRESOLVED: SERIES_COLORS[2],
  DISMISSED: CHART_QUIET,
};

export function RecoveryFunnel({
  funnel, currency,
}: {
  funnel: Partial<Record<FindingStatus, MoneyBucket>>;
  currency: string;
}) {
  const rows = STATUS_ORDER.map(s => ({ status: s, ...(funnel[s] ?? { count: 0, amount_minor: 0 }) }));
  const total = rows.reduce((a, r) => a + Math.max(0, r.amount_minor), 0);
  if (!rows.some(r => r.count > 0)) {
    return <div className="chart-empty" style={{ height: 90 }}>No findings yet. Import an OTA statement to start.</div>;
  }
  return (
    <div role="group" aria-label="Recovery funnel by status">
      <div className="pg-funnel__bar" aria-hidden="true">
        {rows.filter(r => r.amount_minor > 0).map(r => (
          <span key={r.status} title={STATUS_LABEL[r.status]}
                style={{ width: `${(r.amount_minor / (total || 1)) * 100}%`, background: COLOR[r.status] }} />
        ))}
      </div>
      <div className="pg-funnel__rows">
        {rows.filter(r => r.count > 0).map(r => (
          <div key={r.status} className="pg-funnel__row">
            <i style={{ background: COLOR[r.status] }} aria-hidden="true" />
            <span>{STATUS_LABEL[r.status]}</span>
            <span className="pg-faint pg-num">{r.count}</span>
            <span className="pg-num"><Money minor={r.amount_minor} currency={currency} /></span>
          </div>
        ))}
      </div>
    </div>
  );
}
