"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Owner view (05 spec 2.16, deck feature "Owner view: response times by
   team, floor and shift"): first response and completion shown separately,
   SLA breach, reopen and guest-confirmed rates, then the three cuts an
   owner asks for side by side (team, floor, shift) and a breakdown by any
   dimension. One engine (the foundation metrics query over the service.*
   metrics), hotel-local days; requests from before v2 are left out of
   first response rather than counted as instant.

   Labels are for owners, never raw keys: the department dimension reads
   "Team" with the hotel's own team names, floors read "Floor 4" and shifts
   carry their hours (analyticsLabels.ts).
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useMemo, useState } from "react";
import { useHotel } from "../../../../../lib/hotel";
import { useFormat } from "../../../../../lib/format";
import {
  Card, DateRange, EmptyState, LineChart, Segmented, Skeleton, StatTile, Table, lastNDays,
  type Column, type DateRangeValue,
} from "../../../../../ui";
import { ChartCard, HBarChart, CHART_PROGRESS, chartStyles } from "../../../../charts";
import ServiceShell from "../_components/ServiceShell";
import {
  GROUP_LABELS, OWNER_DIMENSIONS, V2_METRICS, fmtMetric, useOwnerView, useV2Analytics, type GroupBy, type GroupRow,
} from "../_components/V2Analytics";
import { distinctLabels, groupLabel, rankBySlowest, shortMinutes, type LabelContext } from "../_components/analyticsLabels";
import { useServiceCatalog } from "../_components/catalog";
import { loadShiftTemplates, type ShiftTemplate } from "../_components/rosterApi";
import { LANGUAGE_LABELS, SOURCE_LABELS } from "../_components/serviceApi";

/** The deck's three cuts first, then the rest. */
const GROUPS: GroupBy[] = ["department", "floor", "shift", "staff", "request_type", "room_type", "language", "stay_stage", "source"];

const OWNER_CARDS: Record<(typeof OWNER_DIMENSIONS)[number], { title: string; empty: string }> = {
  department: { title: "By team", empty: "No acknowledged requests by team in this range." },
  floor: { title: "By floor", empty: "No acknowledged requests with a floor in this range." },
  shift: { title: "By shift", empty: "No acknowledged requests with a shift in this range." },
};

function OwnerCuts({ slug, start, end, ctx }: { slug: string; start: string; end: string; ctx: LabelContext }) {
  const { data, loading, error } = useOwnerView(slug, start, end);
  if (error) return <EmptyState title="Could not load the owner view" body={error} />;
  return (
    <div className="svc-owner-grid">
      {OWNER_DIMENSIONS.map(dim => {
        const rows = distinctLabels(rankBySlowest<GroupRow>(data?.[dim] ?? [], "service.ack_p50_min", 8).map(r => ({
          key: r.key,
          label: groupLabel(dim, r.key, ctx, true),
          values: [Math.max(0, Number(r.values["service.ack_p50_min"] ?? 0))],
          note: `${fmtMetric("count", r.values["service.requests"])} req`,
        })));
        return (
          <ChartCard key={dim} title={OWNER_CARDS[dim].title}
                     hint="First response, median minutes. Slowest on top; the figure after it is the request count.">
            {loading ? <Skeleton height={160} /> : (
              <HBarChart
                rows={rows.map(({ label, values, note }) => ({ label, values, note }))}
                series={[{ label: "First response (median)", color: CHART_PROGRESS }]}
                valueFormat={shortMinutes}
                ariaLabel={`First response by ${GROUP_LABELS[dim].toLowerCase()}`}
                emptyNote={OWNER_CARDS[dim].empty}
              />
            )}
          </ChartCard>
        );
      })}
    </div>
  );
}

export default function AnalyticsConsole() {
  const { slug } = useHotel();
  const hf = useFormat();
  const catalog = useServiceCatalog(slug);
  const [shifts, setShifts] = useState<ShiftTemplate[]>([]);
  useEffect(() => {
    let live = true;
    loadShiftTemplates(slug).then(t => { if (live) setShifts(t); });
    return () => { live = false; };
  }, [slug]);
  const [range, setRange] = useState<DateRangeValue>(() => lastNDays(30, hf.tz));
  const [group, setGroup] = useState<GroupBy>("department");
  const { data, loading, error } = useV2Analytics(slug, range.start, range.end, group, true);
  const t = data?.totals ?? {};

  const ctx = useMemo<LabelContext>(() => ({
    departments: catalog.departments, requestTypes: catalog.requestTypes, shifts,
    languages: LANGUAGE_LABELS, sources: SOURCE_LABELS,
  }), [catalog.departments, catalog.requestTypes, shifts]);

  // Two keys can read the same (a team key and its legacy category): keep the rows apart.
  const rowLabels = useMemo(() => new Map(distinctLabels((data?.groups ?? []).map(r => ({
    key: r.key, label: groupLabel(group, r.key, ctx),
  }))).map(x => [x.key, x.label])), [data, group, ctx]);

  const columns: Column<GroupRow>[] = [
    { key: "key", header: GROUP_LABELS[group], render: r => rowLabels.get(r.key) ?? groupLabel(group, r.key, ctx) },
    ...V2_METRICS.map(m => ({
      key: m.key, header: m.label, align: "right" as const,
      render: (r: GroupRow) => fmtMetric(m.unit, r.values[m.key]),
      hideOnMobile: m.key === "service.ack_p90_min" || m.key === "service.sla_resolve_breach_rate",
    })),
  ];

  const series = data?.daily.length ? [
    { key: "ack", label: "First response, median (min)", points: data.daily.map(d => ({ x: d.day, y: d.ack })) },
    { key: "resolve", label: "Completion, median (min)", points: data.daily.map(d => ({ x: d.day, y: d.resolve })) },
  ] : [];

  return (
    <ServiceShell tab="analytics" perm="service.view" title="Owner view: response times by team, floor and shift"
                  subtitle="How fast someone picks a request up, and how fast it is done, as the guest sees it."
                  actions={<DateRange value={range} onChange={setRange} presets={[7, 30, 90]} />}>
      <style dangerouslySetInnerHTML={{ __html: `${chartStyles}
.svc-owner-grid { display: grid; grid-template-columns: 1fr; gap: 16px; }
@media (min-width: 1000px) { .svc-owner-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }` }} />
      <div className="svc-stack">
        {error && <EmptyState title="Could not load the numbers" body={error} />}
        <div className="svc-stats">
          <StatTile loading={loading} label="First response" value={fmtMetric("minutes", t["service.ack_p50_min"])}
                    sub={`median; 9 in 10 within ${fmtMetric("minutes", t["service.ack_p90_min"])}`} />
          <StatTile loading={loading} label="Completion" value={fmtMetric("minutes", t["service.resolve_p50_min"])}
                    sub="median, request to done" />
          <StatTile loading={loading} label="Acknowledged late" value={fmtMetric("percent", t["service.sla_ack_breach_rate"])}
                    sub={`completed late ${fmtMetric("percent", t["service.sla_resolve_breach_rate"])}`} />
          <StatTile loading={loading} label="Reopened" value={fmtMetric("percent", t["service.reopen_rate"])}
                    sub={`guest confirmed fixed ${fmtMetric("percent", t["service.guest_confirmed_rate"])}; ${fmtMetric("count", t["service.requests"])} requests`} />
        </div>

        <OwnerCuts slug={slug} start={range.start} end={range.end} ctx={ctx} />

        <Card title="Daily medians" subtitle="Hotel-local days. A gap means no acknowledged or completed v2 request that day.">
          {loading ? <Skeleton height={220} /> : (
            <LineChart series={series} ariaLabel="First response and completion medians by day"
                       yFormat={v => `${Math.round(v)}m`} emptyNote="No v2 requests in this range yet." />
          )}
        </Card>

        <Card title="Breakdown" actions={
          <Segmented size="sm" ariaLabel="Group by" value={group} onChange={setGroup}
                     options={GROUPS.map(g => ({ value: g, label: GROUP_LABELS[g] }))} />
        }>
          <Table columns={columns} rows={data?.groups ?? []} rowKey={r => r.key} loading={loading}
                 empty="No requests in this range." caption={`Service metrics by ${GROUP_LABELS[group].toLowerCase()}`} />
          <p className="svc-caption" style={{ marginTop: 8 }}>
            First response counts only requests filed since Service Control v2; older rows are marked pre-v2 and left out.
          </p>
        </Card>
      </div>
    </ServiceShell>
  );
}
