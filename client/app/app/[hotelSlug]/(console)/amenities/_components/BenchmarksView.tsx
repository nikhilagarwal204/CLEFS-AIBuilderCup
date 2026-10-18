"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Cohort benchmarks (02 spec 5.3 benchmarks/): units issued per 100
   occupied room-nights by category, against similar hotels in the same
   city and class. Privacy rules live on the server: a category is only
   published with 3 or more hotels and no single hotel above half the
   volume, and below 5 hotels only a rounded band is shown (never the
   quartiles). Until then the page says so plainly and shows the hotel's
   own learned request rates, which need no cohort.
   ═══════════════════════════════════════════════════════════════════════ */

import { LuUsers } from "react-icons/lu";
import { hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Card, EmptyState, Skeleton, Table, type Column } from "../../../../../ui";
import AmenitiesShell, { PERM_VIEW } from "./AmenitiesShell";
import { categoryLabel, cohortLabel, num, qtyText } from "./lib";
import type { Benchmarks } from "./types";

interface Rate {
  item_id: number;
  name: string;
  category: string;
  requests: string;
  orn: number;
  prior_per_100: string;
  rate_per_100: string;
  raw_per_100: string | null;
  computed_at: string;
}

type BenchRow = Benchmarks["rows"][number];

export default function BenchmarksView() {
  const { slug } = useHotel();
  const bench = useApi<Benchmarks>(hotelPath(slug, "/amenities/benchmarks"), [slug]);
  const rates = useApi<Rate[]>(hotelPath(slug, "/amenities/rates"), [slug]);
  const b = bench.data;
  const rows = (b?.rows ?? []).filter(r => r.published);

  return (
    <AmenitiesShell active="benchmarks" need={[PERM_VIEW]} title="Benchmarks"
                    subtitle="Units issued per 100 occupied room-nights, against similar hotels.">
      <div className="amn-stack">
        {bench.loading && !b ? (
          <Card><Skeleton lines={4} /></Card>
        ) : bench.error ? (
          <Card><p className="amn-note" role="alert">{bench.error.message}</p></Card>
        ) : !b?.available || rows.length === 0 ? (
          <Card>
            <EmptyState
              icon={<LuUsers />}
              title={`Needs ≥${b?.min_hotels ?? 3} hotels in cohort`}
              body={`Benchmarks appear once at least ${b?.min_hotels ?? 3} comparable hotels (${cohortLabel(b?.cohort_key)}) share a month of data and no single hotel dominates it. Your own figures stay private either way.`}
            />
          </Card>
        ) : (
          <BenchTable data={b} rows={rows} />
        )}
        <OwnRates rates={rates.data} loading={rates.loading && !rates.data} />
      </div>
    </AmenitiesShell>
  );
}


// ─── Published cohort rows ───────────────────────────────────────────────

function BenchTable({ data, rows }: { data: Benchmarks; rows: BenchRow[] }) {
  const cols: Column<BenchRow>[] = [
    { key: "cat", header: "Category", render: r => categoryLabel(r.category) },
    { key: "range", header: "Cohort range", render: r => <Band r={r} /> },
    { key: "you", header: "You", align: "right", render: r => (
      <span className="amn-mono">{r.yours_per_100_orn === null ? "No data" : qtyText(r.yours_per_100_orn)}</span>
    ) },
    { key: "n", header: "Hotels", align: "right", hideOnMobile: true, render: r => (
      <span className="amn-chips" style={{ justifyContent: "flex-end" }}>
        {r.hotels_count}
        {r.hotels_count < data.exact_from && <Badge title="Below the exact-figure threshold only a rounded band is shown">band</Badge>}
      </span>
    ) },
  ];
  return (
    <Card title={cohortLabel(data.cohort_key)} subtitle={data.month ? `Month of ${data.month.slice(0, 7)}` : undefined}>
      <Table columns={cols} rows={rows} rowKey={r => r.category} caption="Cohort benchmarks by category" />
      <p className="amn-note">
        The shaded range is the middle half of the cohort (a rounded band below {data.exact_from} hotels); the gold mark
        is you. Units per 100 occupied room-nights.
      </p>
    </Card>
  );
}

function Band({ r }: { r: BenchRow }) {
  const low = num(r.p25 ?? r.band_low);
  const high = num(r.p75 ?? r.band_high);
  const you = r.yours_per_100_orn === null ? null : num(r.yours_per_100_orn);
  const scale = Math.max(high, you ?? 0, 1) * 1.15;
  const pct = (v: number) => `${Math.min(100, (v / scale) * 100)}%`;
  const label = r.p50 !== null ? `${qtyText(low)} to ${qtyText(high)}, median ${qtyText(r.p50)}` : `${qtyText(low)} to ${qtyText(high)}`;
  return (
    <div style={{ minWidth: 140 }}>
      <div className="amn-band" role="img" aria-label={`Cohort ${label}${you !== null ? `, you ${qtyText(you)}` : ""}`}>
        <div className="amn-band__range" style={{ left: pct(low), width: `calc(${pct(high)} - ${pct(low)})` }} />
        {you !== null && <div className="amn-band__you" style={{ left: pct(you) }} />}
      </div>
      <span className="amn-muted">{label}</span>
    </div>
  );
}

// ─── The hotel's own learned rates ───────────────────────────────────────

function OwnRates({ rates, loading }: { rates?: Rate[]; loading: boolean }) {
  const fmt = useFormat();
  const cols: Column<Rate>[] = [
    { key: "name", header: "On-request item", render: r => (
      <span className="amn-rowtitle"><span>{r.name}</span><small>{categoryLabel(r.category)}</small></span>
    ) },
    { key: "rate", header: "Requests per 100 room-nights", align: "right",
      render: r => <span className="amn-mono">{qtyText(r.rate_per_100)}</span> },
    { key: "raw", header: "Raw", align: "right", hideOnMobile: true, render: r => (r.raw_per_100 === null ? "" : qtyText(r.raw_per_100)) },
    { key: "n", header: "Based on", align: "right", hideOnMobile: true,
      render: r => `${qtyText(r.requests)} requests, ${fmt.number(r.orn)} room-nights` },
  ];
  return (
    <Card title="Your request rates" subtitle="Learned weekly from logged requests over 90 days">
      <Table columns={cols} rows={rates ?? []} rowKey={r => r.item_id} loading={loading} caption="Learned request rates"
             empty="No rates yet. They are learned every Monday once requests and occupancy are flowing." />
      <p className="amn-note">
        While room-nights are few, a rate leans on a typical figure for its category and moves toward your own as data grows.
      </p>
    </Card>
  );
}
