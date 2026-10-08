"use client";

/* Costs tab (04 spec 5.10): month-to-date vendor spend against the
   visibility budget (both in USD micros, K3), the latest AI radar batch
   with the degradation steps the budget forced, and the per-hotel monthly
   estimate. Public checks and snapshots are budgeted globally, not here. */

import { hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Badge, Card, StatTile } from "../../../../ui";
import type { AiView, VisSettings } from "./types";
import { budgetUsedPct, microsText, stepText } from "./model";
import { TabState } from "./parts";

const ESTIMATE: [string, string][] = [
  ["Places (map rank)", "$0"],
  ["SerpApi Google Hotels (shared with Demand)", "~$1.5"],
  ["Rank calibration", "~$0.5"],
  ["Google AI Mode (credit cost unverified)", "~$4.2"],
  ["Gemini with Google Search grounding", "~$7-12"],
  ["Perplexity, when enabled", "~$2-3"],
  ["Parsing and fact extraction", "~$1"],
  ["Business Profile, CrUX, PageSpeed", "$0"],
];

export default function CostsTab() {
  const { slug } = useHotel();
  const fmt = useFormat();
  // USD micros through format.ts: grouping and the currency's own digits.
  const usd = (micros: number | null | undefined) => microsText(micros, fmt.money);
  const s = useApi<VisSettings>(hotelPath(slug, "/visibility/settings"), [slug]);
  const a = useApi<AiView>(hotelPath(slug, "/visibility/ai"), [slug]);
  const b = s.data?.budget ?? a.data?.budget ?? null;
  const used = budgetUsedPct(b);
  const batch = a.data?.latest_batch ?? null;
  const steps = Array.isArray(batch?.steps) ? batch.steps : [];

  return (
    <TabState loading={s.loading && !s.data} error={s.error} onRetry={s.reload}>
      <div className="vis-stack">
        <div className="vis-tiles">
          <StatTile label="Spent this month" value={usd(b?.spent_micros)} sub="vendor calls for this hotel" />
          <StatTile label="Monthly budget" value={b?.budget_micros == null ? "n/a" : usd(b.budget_micros)}
            sub={b?.budget_micros == null ? "no exchange rate for the budget currency" : `set as ${s.data?.budget_currency ?? "USD"} in Settings`} />
          <StatTile label="Remaining" value={b?.remaining_micros == null ? "n/a" : usd(Math.max(0, b.remaining_micros))}
            sub={b?.exhausted ? "Budget reached: paid checks pause until next month" : "jobs check this before each call"} />
          <StatTile label="Last AI radar batch" value={batch ? usd(batch.cost_micros) : "n/a"}
            sub={batch ? `${batch.done} of ${batch.planned} answers, ${batch.status}` : "No batch yet"} />
        </div>
        <Card title="Budget used" actions={b?.exhausted ? <Badge tone="bad" dot>Exhausted</Badge> : undefined}>
          <div className="vis-bar" role="img" aria-label={`${used.toFixed(0)}% of the monthly budget used`}>
            <div className={used >= 100 ? "vis-bar__fill vis-bar__fill--over" : "vis-bar__fill"} style={{ width: `${used}%` }} />
          </div>
          <p className="vis-note">
            When the AI radar would exceed the budget it degrades in this order: source-market prompts, then repeats cut
            to 3, then Arabic prompts, then the most expensive engine. A prompt runs all of its repeats or none.
          </p>
          {steps.length > 0 && (
            <ul className="vis-cites" style={{ marginTop: 8 }}>
              {steps.map((st, i) => <li key={i}><Badge tone="warn">This week</Badge> {stepText(st)}</li>)}
            </ul>
          )}
        </Card>
        <Card title="Typical monthly cost per hotel" subtitle="Estimate from about 416 AI answers per engine a month.">
          <dl className="vis-meta">
            {ESTIMATE.map(([k, v]) => <FragmentRow key={k} k={k} v={v} />)}
            <dt><strong>Total</strong></dt><dd><strong>~$14-21</strong></dd>
          </dl>
        </Card>
      </div>
    </TabState>
  );
}

function FragmentRow({ k, v }: { k: string; v: string }) {
  return <><dt>{k}</dt><dd className="vis-num">{v}</dd></>;
}
