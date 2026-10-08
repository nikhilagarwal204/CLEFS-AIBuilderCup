"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Trends (04 spec 6.8 trend/): published months only (confirmed mapping),
   so a figure here is the same one Ask Clefs quotes. GOP against revenue,
   the per-available-room measures, the ratios with the labour band from
   Finance settings, and the verified profit per available room each month
   froze at publish. The weekly NRevPAR series needs only finance.weekly.view,
   so a revenue manager sees that chart alone.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Card, EmptyState, LineChart, Segmented, Skeleton, type LineSeries } from "../../../../../ui";
import { dayFromEpoch, finApi, monthFromEpoch, num, plottableWeeks, weeksAgo } from "../lib";
import type { Monthly, SettingsOut, WeeklyOut } from "../types";
import { errText } from "./bits";

type Span = 6 | 12 | 24;

export default function TrendCharts() {
  const { slug } = useHotel();
  const { can } = useSession();
  const canPL = can("finance.pl.view");
  const canWeekly = can(["finance.weekly.view", "finance.view"]);
  return (
    <>
      {canPL && <MonthlyTrends slug={slug} />}
      {canWeekly && <WeeklyTrend slug={slug} />}
      {!canPL && !canWeekly && (
        <Card><EmptyState title="No access to finance trends" body="Ask the owner to give your role finance access." /></Card>
      )}
    </>
  );
}

// ─── Monthly (published P&L) ─────────────────────────────────────────────

function MonthlyTrends({ slug }: { slug: string }) {
  const fmt = useFormat();
  const [span, setSpan] = useState<Span>(12);
  const res = useApi<{ months: Monthly[] }>(finApi(slug, `/trend?months=${span}`), [slug, span]);
  const cfg = useApi<SettingsOut>(finApi(slug, "/settings"), [slug]);
  const months = useMemo(() => res.data?.months ?? [], [res.data]);
  const ccy = months[months.length - 1]?.currency;
  const pts = (k: keyof Monthly) => months.map(m => ({ x: m.month, y: num(m[k] as string | null) }));
  const money = (v: number) => fmt.money(v, { currency: ccy, compact: true });
  const pct = (v: number) => `${v.toFixed(0)}%`;
  const s = cfg.data?.settings;

  const gop: LineSeries[] = [
    { key: "tor", label: "Total revenue", points: pts("total_revenue") },
    { key: "gop", label: "GOP", points: pts("gop") },
    { key: "np", label: "Net profit", dashed: true, points: pts("net_profit") },
  ];
  const par: LineSeries[] = [
    { key: "trevpar", label: "TRevPAR", points: pts("trevpar") },
    { key: "revpar", label: "RevPAR", points: pts("revpar") },
    { key: "goppar", label: "GOPPAR", points: pts("goppar") },
  ];
  const ratios: LineSeries[] = [
    { key: "margin", label: "GOP margin", points: pts("gop_margin_pct") },
    { key: "labour", label: "Labour cost", points: pts("labour_pct") },
    { key: "eww", label: "Energy, water and waste", points: pts("eww_pct") },
  ];
  const verified: LineSeries[] = [
    { key: "vpar", label: "Verified profit per available room", points: pts("verified_profit_par") },
  ];

  return (
    <section className="cx-stack" aria-label="Monthly trends">
      <div className="fin-row">
        <h2 className="fin-group__head" style={{ margin: 0 }}>Published months</h2>
        <Segmented ariaLabel="How many months" value={span} onChange={setSpan} size="sm"
          options={[{ value: 6, label: "6 months" }, { value: 12, label: "12 months" }, { value: 24, label: "24 months" }]} />
      </div>
      {res.loading && !res.data ? (
        <Skeleton lines={6} />
      ) : res.error ? (
        <Card><EmptyState title="Could not load trends" body={errText(res.error)} /></Card>
      ) : months.length < 2 ? (
        <Card><EmptyState title="Not enough months yet" body="Trends start once two months are published." /></Card>
      ) : (
        <>
          <div className="fin-grid2">
            <Card title="Revenue, GOP and net profit">
              <LineChart series={gop} ariaLabel="Revenue, GOP and net profit by month" yFormat={money} xFormat={monthFromEpoch} />
            </Card>
            <Card title="Per available room">
              <LineChart series={par} ariaLabel="TRevPAR, RevPAR and GOPPAR by month" yFormat={money} xFormat={monthFromEpoch} />
            </Card>
          </div>
          <div className="fin-grid2">
            <Card title="Margins and cost ratios" subtitle={s ? `Shaded: your labour band, ${s.labour_band_min_pct}% to ${s.labour_band_max_pct}% of revenue.` : undefined}>
              <LineChart series={ratios} ariaLabel="GOP margin, labour and energy ratios by month" yFormat={pct} xFormat={monthFromEpoch}
                band={s ? { label: "Labour band", low: s.labour_band_min_pct, high: s.labour_band_max_pct } : undefined} />
            </Card>
            <Card title="Clefs-verified profit per available room" subtitle="As frozen when each month was published.">
              <LineChart series={verified} ariaLabel="Verified profit per available room by month" yFormat={v => fmt.money(v, { currency: ccy })} xFormat={monthFromEpoch} />
            </Card>
          </div>
        </>
      )}
    </section>
  );
}

// ─── Weekly NRevPAR (no P&L needed) ──────────────────────────────────────

type WeekSpan = 13 | 26 | 52;

function WeeklyTrend({ slug }: { slug: string }) {
  const fmt = useFormat();
  const [span, setSpan] = useState<WeekSpan>(26);
  // Anchored on the hotel's calendar day; the server snaps to ISO Mondays and
  // ends on the last complete week.
  const from = weeksAgo(fmt.dayKey(new Date()), span);
  const res = useApi<WeeklyOut>(finApi(slug, `/weekly?from=${from}`), [slug, from]);
  const weeks = useMemo(() => plottableWeeks(res.data?.weeks ?? []), [res.data]);
  const ccy = res.data?.currency;
  const series: LineSeries[] = [
    { key: "nrevpar", label: "NRevPAR", points: weeks.map(w => ({ x: w.week_start, y: num(w.nrevpar) })) },
    { key: "revpar", label: "RevPAR", dashed: true, points: weeks.map(w => ({ x: w.week_start, y: num(w.revpar) })) },
    { key: "otapor", label: "OTA cost per occupied room", points: weeks.map(w => ({ x: w.week_start, y: num(w.ota_cost_por) })) },
  ];
  return (
    <section className="cx-stack" aria-label="Weekly trend">
      <div className="fin-row">
        <h2 className="fin-group__head" style={{ margin: 0 }}>Weekly pulse</h2>
        <Segmented ariaLabel="How many weeks" value={span} onChange={setSpan} size="sm"
          options={[{ value: 13, label: "13 weeks" }, { value: 26, label: "26 weeks" }, { value: 52, label: "52 weeks" }]} />
      </div>
      <Card title="NRevPAR against RevPAR" subtitle="Weeks without available rooms are skipped, not drawn as zero.">
        {res.loading && !res.data ? <Skeleton lines={5} /> : res.error ? (
          <EmptyState title="Could not load the weekly pulse" body={errText(res.error)} />
        ) : (
          <LineChart series={series} ariaLabel="NRevPAR, RevPAR and OTA cost per occupied room by week"
            yFormat={v => fmt.money(v, { currency: ccy, compact: true })} xFormat={dayFromEpoch}
            emptyNote="No week with available rooms yet." />
        )}
      </Card>
    </section>
  );
}
