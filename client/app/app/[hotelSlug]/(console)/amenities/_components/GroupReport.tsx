"use client";

/* Grouped variance report (GET /amenities/report?group=): by item, floor,
   shift or staff over 7/30/90 days ending yesterday.

   Shift and staff views show what was ISSUED, never a variance: expected
   use is a per-day, per-floor figure, so splitting it by shift or person
   would invent numbers. The staff view is neutral by design (02 spec 5.2):
   it only appears with 14+ days and 3+ issuers, it reads "issued per
   occupied room served", and when one issuer holds most of the excess the
   advice is to rotate who issues and counts, never a name to blame. */

import Link from "next/link";
import { useState } from "react";
import { hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Card, Segmented, Table, type Column } from "../../../../../ui";
import { amnHref } from "./AmenitiesShell";
import { categoryLabel, coverageNote, floorLabel, num, qtyText, shiftLabel, varianceTone, windowEndingYesterday } from "./lib";
import type { FloorRow, GroupReport, ItemRow, ShiftReport, StaffReport } from "./types";

type Group = "item" | "floor" | "shift" | "staff";
const GROUPS: { value: Group; label: string }[] = [
  { value: "item", label: "Item" }, { value: "floor", label: "Floor" },
  { value: "shift", label: "Shift" }, { value: "staff", label: "Staff" },
];
const WINDOWS = [7, 30, 90] as const;

export default function GroupReportCard() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [group, setGroup] = useState<Group>("item");
  const [days, setDays] = useState<number>(30);
  const win = windowEndingYesterday(days, fmt.dayKey(new Date()));
  const path = hotelPath(slug, `/amenities/report?group=${group}&from=${win.from}&to=${win.to}`);
  const rep = useApi<GroupReport>(path, [slug]);

  const cover = rep.data && "days_covered" in rep.data
    ? coverageNote(rep.data.days_covered, rep.data.days_missing_occupancy) : null;

  return (
    <Card title="Breakdown" subtitle={`${win.from} to ${win.to}`}
          actions={
            <Segmented size="sm" ariaLabel="Window" value={days} onChange={setDays}
                       options={WINDOWS.map(n => ({ value: n, label: `${n}d` }))} />
          }>
      <div className="amn-toolbar">
        <Segmented ariaLabel="Group by" value={group} onChange={setGroup} options={GROUPS} />
      </div>
      {cover && <p className="amn-note" role="status">{cover}</p>}
      {rep.error ? (
        <p className="amn-note" role="alert">{rep.error.message}</p>
      ) : group === "item" ? (
        <ItemTable rows={rep.data?.group === "item" ? rep.data.rows : []} loading={rep.loading} slug={slug} />
      ) : group === "floor" ? (
        <FloorTable rows={rep.data?.group === "floor" ? rep.data.rows : []} loading={rep.loading} />
      ) : group === "shift" ? (
        <ShiftTable data={rep.data?.group === "shift" ? rep.data : undefined} loading={rep.loading} />
      ) : (
        <StaffTable data={rep.data?.group === "staff" ? rep.data : undefined} loading={rep.loading} />
      )}
    </Card>
  );
}

// ─── Shared cells ────────────────────────────────────────────────────────

function VarianceCell({ value, pct }: { value: string | null; pct: string | null }) {
  const fmt = useFormat();
  const n = num(value);
  const tone = varianceTone(pct);
  return (
    <span className="amn-mono" style={{ display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "flex-end" }}>
      <span className={n > 0 ? "amn-leak" : undefined}>{fmt.money(n, { signDisplay: "exceptZero" })}</span>
      {pct !== null && <Badge tone={tone === "neutral" ? "neutral" : tone}>{num(pct) > 0 ? "+" : ""}{pct}%</Badge>}
    </span>
  );
}

// ─── Item and floor ──────────────────────────────────────────────────────

function ItemTable({ rows, loading, slug }: { rows: ItemRow[]; loading: boolean; slug: string }) {
  const fmt = useFormat();
  const cols: Column<ItemRow>[] = [
    { key: "name", header: "Item", render: r => (
      <span className="amn-rowtitle">
        <Link className="amn-link" href={amnHref(slug, `/items/${r.item_id}`)}>{r.name}</Link>
        <small>{categoryLabel(r.category)}</small>
      </span>
    ) },
    { key: "expected", header: "Expected", align: "right", render: r => qtyText(r.expected) },
    { key: "requested", header: "Requested", align: "right", hideOnMobile: true, render: r => qtyText(r.requested) },
    { key: "issued", header: "Issued", align: "right", render: r => qtyText(r.issued) },
    { key: "excluded", header: "VIP and damage", align: "right", hideOnMobile: true,
      render: r => (num(r.issued_excluded) ? qtyText(r.issued_excluded) : "") },
    { key: "cost", header: "Issued cost", align: "right", hideOnMobile: true, render: r => fmt.money(r.issued_value) },
    { key: "variance", header: "Above expected", align: "right",
      render: r => <VarianceCell value={r.variance_value} pct={r.variance_pct} /> },
  ];
  return <Table columns={cols} rows={rows} rowKey={r => r.item_id} loading={loading} caption="Variance by item"
                empty="No settled days in this window yet. Figures appear after the nightly rollup." />;
}

function FloorTable({ rows, loading }: { rows: FloorRow[]; loading: boolean }) {
  const fmt = useFormat();
  const cols: Column<FloorRow>[] = [
    { key: "floor", header: "Floor", render: r => floorLabel(r.floor) },
    { key: "expected", header: "Expected", align: "right", render: r => qtyText(r.expected) },
    { key: "issued", header: "Issued", align: "right", render: r => qtyText(r.issued) },
    { key: "cost", header: "Issued cost", align: "right", hideOnMobile: true, render: r => fmt.money(r.issued_value) },
    { key: "variance", header: "Above expected", align: "right",
      render: r => <VarianceCell value={r.variance_value} pct={r.variance_pct} /> },
  ];
  return (
    <>
      <Table columns={cols} rows={rows} rowKey={(r, i) => r.floor ?? `none-${i}`} loading={loading}
             caption="Variance by floor" empty="No settled days in this window yet." />
      <p className="amn-note">
        Lines without a floor are compared with the whole hotel. Tag the floor when issuing to see where use sits.
      </p>
    </>
  );
}

// ─── Shift and staff ─────────────────────────────────────────────────────

function ShiftTable({ data, loading }: { data?: ShiftReport; loading: boolean }) {
  const fmt = useFormat();
  type Row = ShiftReport["rows"][number];
  const cols: Column<Row>[] = [
    { key: "shift", header: "Shift", render: r => shiftLabel(r.shift) },
    { key: "issued", header: "Issued", align: "right", render: r => qtyText(r.issued) },
    { key: "lines", header: "Lines", align: "right", hideOnMobile: true, render: r => fmt.number(r.lines) },
    { key: "cost", header: "Issued cost", align: "right", render: r => fmt.money(r.issued_value) },
    { key: "share", header: "Share", align: "right", render: r => (r.share_pct ? `${r.share_pct}%` : "") },
  ];
  return (
    <>
      <Table columns={cols} rows={data?.rows ?? []} rowKey={r => r.shift} loading={loading && !data}
             caption="Issuance by shift" empty="No issuance in this window." />
      {data?.note && <p className="amn-note">{data.note}</p>}
    </>
  );
}

function StaffTable({ data, loading }: { data?: StaffReport; loading: boolean }) {
  const fmt = useFormat();
  type Row = StaffReport["rows"][number];
  if (data && !data.available) {
    return <p className="amn-note" role="status">{data.reason ?? "Not enough issuance yet for a fair comparison."}</p>;
  }
  const cols: Column<Row>[] = [
    { key: "name", header: "Issued by", render: r => r.staff_name },
    { key: "per", header: "Per occupied room served", align: "right", render: r => r.issued_per_room ?? "" },
    { key: "issued", header: "Issued", align: "right", render: r => qtyText(r.issued) },
    { key: "days", header: "Days", align: "right", hideOnMobile: true, render: r => fmt.number(r.days) },
    { key: "floors", header: "Floors", hideOnMobile: true, render: r => r.floors.join(", ") },
    { key: "cost", header: "Issued cost", align: "right", hideOnMobile: true, render: r => fmt.money(r.issued_value) },
  ];
  return (
    <>
      <Table columns={cols} rows={data?.rows ?? []} rowKey={r => r.staff_name} loading={loading && !data}
             caption="Issuance by person" empty="No named issuance in this window." />
      {data?.note && <p className="amn-note">{data.note}</p>}
      {data?.counter_rotation && data.counter_rotation_note && (
        <div className="amn-banner" role="status" style={{ marginTop: 10 }}>{data.counter_rotation_note}</div>
      )}
    </>
  );
}
