"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Service analytics v2 (03 spec 1.7 metrics, 1.8 "ack vs completion shown
   separately").

   Built on the foundation metrics engine (POST /hotels/{slug}/metrics/query)
   with the service.* metrics registered by the service lane, so the numbers
   here, in Ask and in the Brief are the same SQL. Buckets are hotel-local
   days (the engine applies the hotel timezone). First response is
   acknowledged - created; completion is resolved - created, as the guest
   sees it. Pre-v2 rows never count toward first response.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useEffect, useState } from "react";
import { useFormat } from "../../../../../lib/format";
import { hotelDayKey, addDays } from "./hotelTime";
import { useNow } from "./RequestMeta";
import { ApiError, api, hotelPath } from "../../../../../lib/api";

// ─── Types ───

export type GroupBy =
  | "staff" | "floor" | "shift" | "department" | "room_type" | "language" | "stay_stage" | "source" | "request_type";

export const GROUP_LABELS: Record<GroupBy, string> = {
  staff: "Staff", floor: "Floor", shift: "Shift", department: "Department", room_type: "Room type",
  language: "Language", stay_stage: "Stay stage", source: "Channel", request_type: "Request type",
};

export const V2_METRICS = [
  { key: "service.requests", label: "Requests", unit: "count" },
  { key: "service.ack_p50_min", label: "First response (median)", unit: "minutes" },
  { key: "service.ack_p90_min", label: "First response (p90)", unit: "minutes" },
  { key: "service.resolve_p50_min", label: "Completion (median)", unit: "minutes" },
  { key: "service.sla_ack_breach_rate", label: "Acked late", unit: "percent" },
  { key: "service.sla_resolve_breach_rate", label: "Completed late", unit: "percent" },
  { key: "service.reopen_rate", label: "Reopened", unit: "percent" },
  { key: "service.guest_confirmed_rate", label: "Guest confirmed", unit: "percent" },
] as const;

export type MetricKey = (typeof V2_METRICS)[number]["key"];

interface MetricRow { bucket?: string | null; group?: Record<string, string | null>; value: number | null }
interface MetricResult { rows: MetricRow[]; total?: number | null; truncated?: boolean }

export interface GroupRow { key: string; values: Partial<Record<MetricKey, number | null>> }

export interface V2Result {
  totals: Partial<Record<MetricKey, number | null>>;
  groups: GroupRow[];
  daily: { day: string; requests: number | null; ack: number | null; resolve: number | null }[];
}

// ─── Query ───

function query(slug: string, metric: string, start: string, end: string, grain: "total" | "day", groupBy?: GroupBy) {
  return api.post<MetricResult>(hotelPath(slug, "/metrics/query"), {
    metric, start, end, grain, group_by: groupBy ? [groupBy] : [],
  }, { silent401: true });
}

/** Total of a grain=total, ungrouped result (the engine returns one row). */
function single(r: MetricResult | null): number | null {
  if (!r) return null;
  if (r.total !== undefined && r.total !== null) return r.total;
  return r.rows?.[0]?.value ?? null;
}

export async function loadV2(slug: string, start: string, end: string, groupBy?: GroupBy, withDaily = false): Promise<V2Result> {
  const safe = (p: Promise<MetricResult>) => p.catch(err => {
    // A metric this viewer may not read, or one not registered yet, is a gap, not a failure.
    if (err instanceof ApiError && [400, 401, 403, 404].includes(err.status)) return null;
    throw err;
  });
  const totals = await Promise.all(V2_METRICS.map(m => safe(query(slug, m.key, start, end, "total"))));
  const out: V2Result = { totals: {}, groups: [], daily: [] };
  V2_METRICS.forEach((m, i) => { out.totals[m.key] = single(totals[i]); });

  if (groupBy) {
    const grouped = await Promise.all(V2_METRICS.map(m => safe(query(slug, m.key, start, end, "total", groupBy))));
    const byKey = new Map<string, GroupRow>();
    V2_METRICS.forEach((m, i) => {
      for (const row of grouped[i]?.rows ?? []) {
        const key = row.group?.[groupBy] ?? "(none)";
        const g = byKey.get(key) ?? { key, values: {} };
        g.values[m.key] = row.value;
        byKey.set(key, g);
      }
    });
    out.groups = [...byKey.values()].sort(
      (a, b) => (b.values["service.requests"] ?? 0) - (a.values["service.requests"] ?? 0));
  }

  if (withDaily) {
    const [req, ack, res] = await Promise.all([
      safe(query(slug, "service.requests", start, end, "day")),
      safe(query(slug, "service.ack_p50_min", start, end, "day")),
      safe(query(slug, "service.resolve_p50_min", start, end, "day")),
    ]);
    const days = new Map<string, V2Result["daily"][number]>();
    const put = (r: MetricResult | null, field: "requests" | "ack" | "resolve") => {
      for (const row of r?.rows ?? []) {
        const day = String(row.bucket ?? "").slice(0, 10);
        if (!day) continue;
        const d = days.get(day) ?? { day, requests: null, ack: null, resolve: null };
        d[field] = row.value;
        days.set(day, d);
      }
    };
    put(req, "requests"); put(ack, "ack"); put(res, "resolve");
    out.daily = [...days.values()].sort((a, b) => a.day.localeCompare(b.day));
  }
  return out;
}

export function useV2Analytics(slug: string, start: string, end: string, groupBy?: GroupBy, withDaily = false) {
  const [state, setState] = useState<{ data: V2Result | null; error: string | null; loading: boolean; key: string }>(
    { data: null, error: null, loading: true, key: "" });
  const key = `${slug}|${start}|${end}|${groupBy ?? ""}|${withDaily}`;
  useEffect(() => {
    if (!start || !end) return; // range not known yet (the clock starts after mount)
    let live = true;
    loadV2(slug, start, end, groupBy, withDaily)
      .then(data => { if (live) setState({ data, error: null, loading: false, key }); })
      .catch(err => { if (live) setState({ data: null, error: String((err as Error)?.message ?? err), loading: false, key }); });
    return () => { live = false; };
  }, [slug, start, end, groupBy, withDaily, key]);
  return { ...state, loading: state.key !== key || state.loading };
}

// ─── Formatting ───

export function fmtMetric(unit: string, v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (unit === "percent") return `${Math.round(v * 10) / 10}%`;
  if (unit === "minutes") return v < 1 ? "<1 min" : v < 60 ? `${Math.round(v * 10) / 10} min` : `${Math.floor(v / 60)}h ${Math.round(v % 60)}m`;
  return String(Math.round(v));
}

// ─── Strip for the classic dashboard's analytics view ───

/** Ack and completion side by side for the last 7 hotel-local days, with a
    link to the full analytics page. Silent (renders nothing) while the
    metrics are unavailable, so the legacy view still stands on its own. */
export function V2AnalyticsStrip({ hotelSlug }: { hotelSlug: string }) {
  const hf = useFormat();
  const now = useNow(300_000);
  const end = now ? hotelDayKey(now, hf.tz) : "";
  const start = end ? addDays(end, -6) : "";
  const { data, loading } = useV2Analytics(hotelSlug, start, end);
  if (!now || loading || !data) return null;
  const t = data.totals;
  if (t["service.requests"] == null && t["service.ack_p50_min"] == null) return null;
  const tiles: { label: string; value: string; sub: string }[] = [
    { label: "First response", value: fmtMetric("minutes", t["service.ack_p50_min"]), sub: `median; 9 in 10 within ${fmtMetric("minutes", t["service.ack_p90_min"])}` },
    { label: "Completion", value: fmtMetric("minutes", t["service.resolve_p50_min"]), sub: "median, request to done" },
    { label: "Acked late", value: fmtMetric("percent", t["service.sla_ack_breach_rate"]), sub: `completed late ${fmtMetric("percent", t["service.sla_resolve_breach_rate"])}` },
    { label: "Reopened", value: fmtMetric("percent", t["service.reopen_rate"]), sub: `guest confirmed ${fmtMetric("percent", t["service.guest_confirmed_rate"])}` },
  ];
  return (
    <div className="glass-card" style={{ padding: "18px 20px", marginBottom: "20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <strong>Last 7 days: first response vs completion</strong>
        <Link className="svc-linkbtn" href={`/app/${encodeURIComponent(hotelSlug)}/service/analytics`}>Full service analytics</Link>
      </div>
      <div className="svc-stats">
        {tiles.map(x => (
          <div key={x.label}>
            <div className="svc-faint">{x.label}</div>
            <div style={{ fontSize: "1.5rem", fontWeight: 700 }}>{x.value}</div>
            <div className="svc-faint">{x.sub}</div>
          </div>
        ))}
      </div>
      <div className="svc-caption" style={{ marginTop: 8 }}>Requests from before Service Control v2 are left out of first response.</div>
    </div>
  );
}
