"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One item over time (02 spec 5.3, items/[id]): issued vs expected vs
   requested per day, then where it went by floor and by shift, then the
   raw lines so a manager can see (and void) a mistyped entry.

   Days without occupancy are absent from the series (the server leaves
   them out rather than zero-filling), so the line simply skips them.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuArrowLeft } from "react-icons/lu";
import { hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, LineChart, Segmented, Skeleton, type LineSeries } from "../../../../../ui";
import { CHART_NEW, CHART_PROGRESS, HBarChart } from "../../../../charts";
import AmenitiesShell, { AmnEmpty, amnHref, PERM_VIEW } from "./AmenitiesShell";
import IssuanceLog from "./IssuanceLog";
import { BASIS_LABELS, CHART_EXPECTED, categoryLabel, floorLabel, num, qtyText, shiftLabel, windowEndingYesterday } from "./lib";
import type { Trend } from "./types";

const WINDOWS = [28, 56, 90] as const;

export default function ItemTrend({ itemId }: { itemId: string }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [days, setDays] = useState<number>(56);
  const id = /^\d+$/.test(itemId) ? itemId : null;
  const win = windowEndingYesterday(days, fmt.dayKey(new Date()));
  const trend = useApi<Trend>(
    id ? hotelPath(slug, `/amenities/items/${id}/trend?from=${win.from}&to=${win.to}`) : null, [slug],
  );
  const item = trend.data?.item;

  const back = (
    <Button size="sm" variant="ghost" icon={<LuArrowLeft />} href={amnHref(slug)}>Overview</Button>
  );

  if (!id || trend.error?.status === 404) {
    return (
      <AmenitiesShell active="item" need={[PERM_VIEW]} title="Item not found" actions={back}>
        <Card><AmnEmpty title="This item does not exist" body="It may have been deleted from the catalog." /></Card>
      </AmenitiesShell>
    );
  }

  return (
    <AmenitiesShell
      active="item" need={[PERM_VIEW]} actions={back}
      title={item ? item.name_en : "Item"}
      subtitle={item ? <ItemFacts item={item} /> : undefined}
    >
      {trend.error ? (
        <Card><AmnEmpty title="Could not load this item" body={trend.error.message}
                        action={<Button onClick={trend.reload}>Try again</Button>} /></Card>
      ) : (
        <div className="amn-stack">
          <Card title="Issued against expected" subtitle={`${win.from} to ${win.to}, units per day`}
                actions={<Segmented size="sm" ariaLabel="Window" value={days} onChange={setDays}
                                    options={WINDOWS.map(n => ({ value: n, label: `${n}d` }))} />}>
            {trend.loading && !trend.data ? <Skeleton height={220} /> : <TrendChart data={trend.data} />}
          </Card>
          <div className="amn-grid2" style={{ marginBottom: 0 }}>
            <ByFloor data={trend.data} />
            <ByShift data={trend.data} />
          </div>
          {item && <IssuanceLog itemId={item.id} from={win.from} to={win.to} />}
        </div>
      )}
    </AmenitiesShell>
  );
}

// ─── Facts line ──────────────────────────────────────────────────────────

function ItemFacts({ item }: { item: Trend["item"] }) {
  const fmt = useFormat();
  const std = item.basis === "ON_REQUEST" ? "on request" : `${qtyText(item.std_qty)} ${BASIS_LABELS[item.basis]?.toLowerCase() ?? ""}`;
  return (
    <span className="amn-chips" style={{ alignItems: "center" }}>
      <span>{categoryLabel(item.category)}, {std}, {item.unit_cost ? fmt.money(item.unit_cost, { maximumFractionDigits: 2 }) : "no cost"} each</span>
      {item.name_ar && <span lang="ar" dir="rtl">{item.name_ar}</span>}
      {item.is_single_use_plastic && <Badge tone="warn">Single-use plastic</Badge>}
      {item.needs_review && <Badge tone="gold">Default values, confirm in catalog</Badge>}
      {!item.active && <Badge>Inactive</Badge>}
    </span>
  );
}

// ─── Charts ──────────────────────────────────────────────────────────────

function TrendChart({ data }: { data?: Trend }) {
  const fmt = useFormat();
  const s = data?.series ?? [];
  const series: LineSeries[] = [
    { key: "issued", label: "Issued", color: CHART_PROGRESS, points: s.map(p => ({ x: p.date, y: num(p.issued) })) },
    { key: "expected", label: "Expected", color: CHART_EXPECTED, dashed: true,
      points: s.map(p => ({ x: p.date, y: num(p.expected) })) },
    { key: "requested", label: "Requested", color: CHART_NEW, points: s.map(p => ({ x: p.date, y: num(p.requested) })) },
  ];
  const total = s.reduce((acc, p) => acc + num(p.variance_value), 0);
  return (
    <>
      <LineChart series={series} ariaLabel="Issued, expected and requested units per day"
                 yFormat={v => fmt.number(v)} emptyNote="No settled days with occupancy in this window." />
      {s.length > 0 && (
        <p className="amn-note">
          {s.length} settled days. Above expected over the window:{" "}
          <span className={total > 0 ? "amn-leak" : undefined}>{fmt.money(total, { signDisplay: "exceptZero" })}</span>.
          VIP set-ups and damage write-offs are explained use and sit outside issued.
        </p>
      )}
    </>
  );
}

function ByFloor({ data }: { data?: Trend }) {
  const rows = (data?.by_floor ?? []).map(f => ({
    label: floorLabel(f.floor),
    values: [Math.round(num(f.issued))],
    note: `exp ${Math.round(num(f.expected))}`,
  }));
  return (
    <Card title="By floor" subtitle="Units issued, with expected alongside">
      {!data ? <Skeleton lines={4} /> : (
        <HBarChart rows={rows} series={[{ label: "Issued", color: CHART_PROGRESS }]} ariaLabel="Units issued by floor"
                   emptyNote="Nothing issued to a floor in this window." />
      )}
    </Card>
  );
}

function ByShift({ data }: { data?: Trend }) {
  const rows = (data?.by_shift ?? []).map(s => ({ label: shiftLabel(s.shift), values: [Math.round(num(s.issued))] }));
  return (
    <Card title="By shift" subtitle="Units issued; expected use is per day, so shifts compare on issued only">
      {!data ? <Skeleton lines={3} /> : (
        <HBarChart rows={rows} series={[{ label: "Issued", color: CHART_NEW }]} ariaLabel="Units issued by shift"
                   emptyNote="Nothing issued in this window." />
      )}
    </Card>
  );
}
