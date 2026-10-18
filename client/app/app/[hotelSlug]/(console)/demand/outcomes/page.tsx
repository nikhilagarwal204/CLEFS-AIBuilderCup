"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Outcomes (02 spec 4.3 measurement): actionability first (how fast
   suggestions were decided and how many were applied), then each measured
   night: pickup after the decision against comparable nights at the same
   lead, occupancy, ADR, RevPAR and net revenue. Every figure is observed,
   not causal, and the label says so on the page and on the table.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, EmptyState, Segmented, StatTile, Table, type Column } from "../../../../../ui";
import { addDays, num, REC_STATUS_LABEL, REC_STATUS_TONE } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { OutcomesResponse } from "../_lib/types";

type Back = 30 | 90 | 180;
type Row = OutcomesResponse["outcomes"][number];

export default function OutcomesPage() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [back, setBack] = useState<Back>(90);
  const [today] = useState(() => fmt.dayKey(Date.now()));
  const out = useApi<OutcomesResponse>(withQuery(drApi(slug, "/outcomes"), { from: addDays(today, -back), to: today }), [slug, back, today]);
  const a = out.data?.actionability;
  const label = out.data?.label ?? "Observed, not causal";
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${fmt.number(v, 1)}%`);
  const money = (v: string | null | undefined, ccy?: string) => fmt.money(v ?? null, { maximumFractionDigits: 0, currency: ccy });

  const columns: Column<Row>[] = [
    { key: "night", header: "Night", render: r => fmt.date(r.stay_date, "medium") },
    { key: "room", header: "Room", render: r => r.room_type_code, hideOnMobile: true },
    {
      key: "decision", header: "Decision",
      render: r => (
        <span className="cx-row" style={{ gap: 6 }}>
          <Badge tone={REC_STATUS_TONE[r.status]}>{REC_STATUS_LABEL[r.status]}</Badge>
          <span className="dr-small">{money(r.current_bar, r.currency)} to {money(r.decided ?? r.recommended, r.currency)}</span>
        </span>
      ),
    },
    {
      key: "pickup", header: "Pickup vs comparable", align: "right",
      render: r => {
        const o = r.outcome;
        const d = num(o.pickup_vs_baseline);
        if (o.pickup_after_decision === null || o.pickup_after_decision === undefined) return <span className="dr-faint">no data</span>;
        return (
          <span title={`${o.pickup_after_decision} rooms picked up after the decision; comparable nights ${o.baseline_pickup ?? "unknown"}`}>
            {o.pickup_after_decision}{d !== null ? ` (${d > 0 ? "+" : ""}${d})` : ""}
          </span>
        );
      },
    },
    { key: "occ", header: "Occupancy", align: "right", render: r => (r.outcome.occupancy === null ? "-" : fmt.pct(r.outcome.occupancy, { ratio: true, digits: 0 })) },
    { key: "adr", header: "ADR", align: "right", render: r => money(r.outcome.adr, r.currency), hideOnMobile: true },
    { key: "revpar", header: "RevPAR", align: "right", render: r => money(r.outcome.revpar, r.currency), hideOnMobile: true },
    { key: "net", header: "Net revenue", align: "right", render: r => (r.outcome.net_revenue ? money(r.outcome.net_revenue.net, r.outcome.net_revenue.currency) : "-"), hideOnMobile: true },
  ];

  return (
    <>
      <div className="cx-row" style={{ justifyContent: "space-between" }}>
        <p className="dr-note">
          <Badge tone="info">{label}</Badge> Nights already stayed. Outcomes show what happened next to each decision, not what the decision caused.
        </p>
        <Segmented<Back> size="sm" ariaLabel="Look back" value={back} onChange={setBack}
                         options={[{ value: 30, label: "30 days" }, { value: 90, label: "90" }, { value: 180, label: "180" }]} />
      </div>

      <div className="cx-grid cx-grid--4">
        <StatTile label="Decided within 48 h" value={pct(a?.decided_within_48h_pct)} loading={out.loading && !out.data}
                  sub={a ? `${a.decided} of ${a.recommendations} suggestions` : undefined} />
        <StatTile label="Applied" value={pct(a?.applied_pct)} loading={out.loading && !out.data}
                  sub={a ? `${a.applied} of ${a.approved} approved, seen in your BAR` : undefined} />
        <StatTile label="Median time to decide" value={a?.median_hours_to_decision === null || a?.median_hours_to_decision === undefined ? "-" : `${fmt.number(a.median_hours_to_decision, 1)} h`}
                  loading={out.loading && !out.data} />
        <StatTile label="Rejected / expired" value={a ? `${a.rejected} / ${a.expired}` : "-"} loading={out.loading && !out.data}
                  sub="Expired: the night passed undecided" />
      </div>

      <Card title="Measured nights" subtitle={`${label}. Pickup compares rooms sold after the decision with the same weekday over the 8 weeks before, at the same lead time.`}>
        {out.error ? (
          <EmptyState title="Could not load outcomes" body={errorText(out.error)} action={<Button onClick={out.reload}>Try again</Button>} />
        ) : (
          <Table<Row>
            columns={columns}
            rows={out.data?.outcomes ?? []}
            rowKey={r => r.id}
            loading={out.loading && !out.data}
            caption={`Measured nights, ${label.toLowerCase()}`}
            empty="No measured nights yet. A night is measured the day after the stay."
          />
        )}
      </Card>
    </>
  );
}
