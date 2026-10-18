"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Service analytics (03 spec 1.7): first response and completion shown
   separately, SLA breach, reopen and guest-confirmed rates, grouped by
   staff, floor, shift, department, room type, language, stay stage or
   channel. One engine (the foundation metrics query over the service.*
   metrics), hotel-local days; requests from before v2 are left out of
   first response rather than counted as instant.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useHotel } from "../../../../../lib/hotel";
import { useFormat } from "../../../../../lib/format";
import {
  Card, DateRange, EmptyState, LineChart, Segmented, Skeleton, StatTile, Table, lastNDays,
  type Column, type DateRangeValue,
} from "../../../../../ui";
import ServiceShell from "../_components/ServiceShell";
import { GROUP_LABELS, V2_METRICS, fmtMetric, useV2Analytics, type GroupBy, type GroupRow } from "../_components/V2Analytics";
import { LANGUAGE_LABELS, SOURCE_LABELS } from "../_components/serviceApi";

const GROUPS: GroupBy[] = ["staff", "department", "floor", "shift", "room_type", "language", "stay_stage", "source", "request_type"];

function groupLabel(group: GroupBy, key: string): string {
  if (key === "(none)" || key === "null") return "Not recorded";
  if (group === "language") return LANGUAGE_LABELS[key] ?? key;
  if (group === "source") return SOURCE_LABELS[key] ?? key;
  return key.replace(/_/g, " ");
}

export default function AnalyticsConsole() {
  const { slug } = useHotel();
  const hf = useFormat();
  const [range, setRange] = useState<DateRangeValue>(() => lastNDays(30, hf.tz));
  const [group, setGroup] = useState<GroupBy>("staff");
  const { data, loading, error } = useV2Analytics(slug, range.start, range.end, group, true);
  const t = data?.totals ?? {};

  const columns: Column<GroupRow>[] = [
    { key: "key", header: GROUP_LABELS[group], render: r => groupLabel(group, r.key) },
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
    <ServiceShell tab="analytics" perm="service.view" title="Service analytics"
                  subtitle="How fast someone picks a request up, and how fast it is done, as the guest sees it."
                  actions={<DateRange value={range} onChange={setRange} presets={[7, 30, 90]} />}>
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
