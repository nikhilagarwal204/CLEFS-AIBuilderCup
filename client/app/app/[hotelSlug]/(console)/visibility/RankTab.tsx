"use client";

/* Rank tab (04 spec 5.3): an internal KPI from Places Text Search on a grid
   around the hotel, per keyword. A table with SoLV sparklines and the
   monthly calibration status; deliberately no heatmap (the grid is a
   measurement device, not a claim about where guests stand). */

import { hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Badge, Card, Sparkline, Table, type Column } from "../../../../ui";
import type { RankKeyword, RankStats, RankView } from "./types";
import { CALIBRATION_TEXT, num, ratioPct, solvSeries } from "./model";
import { SetupChecklist, SourceLabel, TabState } from "./parts";

type Row = { key: string; keyword: string; who: string; self: boolean; stats: RankStats | null; kw: RankKeyword };

function rowsOf(keywords: RankKeyword[]): Row[] {
  const out: Row[] = [];
  for (const kw of keywords) {
    out.push({ key: `${kw.keyword}:self`, keyword: kw.keyword, who: "You", self: true, stats: kw.latest, kw });
    for (const c of kw.latest?.competitors ?? []) {
      out.push({ key: `${kw.keyword}:${c.comp_id}`, keyword: kw.keyword, who: c.name ?? c.comp_id, self: false, stats: c, kw });
    }
  }
  return out;
}

export default function RankTab({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const r = useApi<RankView>(hotelPath(slug, "/visibility/rank"), [slug]);
  const dec = (v: RankStats["arp"], d = 1) => { const n = num(v); return n === null ? "n/a" : n.toFixed(d); };

  const cols: Column<Row>[] = [
    { key: "keyword", header: "Keyword", render: x => (x.self ? <strong>{x.keyword}</strong> : <span className="vis-muted">{x.keyword}</span>) },
    { key: "who", header: "Hotel", render: x => (x.self ? <strong>{x.who}</strong> : x.who) },
    { key: "solv", header: "Share of local voice", align: "right",
      render: x => <span className="vis-num">{ratioPct(x.stats?.solv ?? null, 1)}</span> },
    { key: "atrp", header: "Avg position", align: "right", render: x => <span className="vis-num">{dec(x.stats?.atrp ?? null)}</span> },
    { key: "found", header: "Found at", align: "right", hideOnMobile: true,
      render: x => (x.stats ? `${x.stats.found} of ${x.stats.points} points` : "n/a") },
    { key: "trend", header: "SoLV trend", hideOnMobile: true,
      render: x => (x.self && x.kw.history.length > 1 ? <Sparkline values={solvSeries(x.kw.history)} ariaLabel={`SoLV trend for ${x.keyword}`} /> : null) },
    { key: "cal", header: "Calibration",
      render: x => {
        if (!x.self) return null;
        const c = CALIBRATION_TEXT[x.kw.calibration.status] ?? CALIBRATION_TEXT.uncalibrated;
        return <Badge tone={c.tone} title={x.kw.calibration.note ?? x.kw.calibration.reason}>{c.label}</Badge>;
      } },
  ];

  return (
    <TabState loading={r.loading && !r.data} error={r.error} onRetry={r.reload}>
      {r.data && (
        <div className="vis-stack">
          <SetupChecklist title="Set up map rank" codes={r.data.setup_missing} onOpenSettings={onOpenSettings} />
          <Card title="Map rank by keyword"
            subtitle={`Places search from a ${r.data.grid.size}x${r.data.grid.size} grid, ${r.data.grid.spacing_m} m apart, weekly on Monday. Average position counts not-found as 21.`}>
            <div className="vis-scroll">
              <Table columns={cols} rows={rowsOf(r.data.keywords)} rowKey={x => x.key} caption="Map rank per keyword"
                empty="No rank runs yet." />
            </div>
            <div style={{ marginTop: 10 }}><SourceLabel>{r.data.label}</SourceLabel></div>
            {r.data.keywords.map(k => k.latest && (
              <p key={k.keyword} className="vis-note">{k.keyword}: last run {fmt.dateTime(k.latest.run_at)}
                {k.calibration.status !== "uncalibrated" && k.calibration.spearman != null
                  ? `; calibration Spearman ${dec(k.calibration.spearman, 2)}, top-3 agreement ${dec(k.calibration.top3_agreement ?? null, 2)}` : ""}.</p>
            ))}
          </Card>
        </div>
      )}
    </TabState>
  );
}
