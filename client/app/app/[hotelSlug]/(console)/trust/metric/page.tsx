"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Metric query viewer: where every ROI report figure links. It re-runs the
   exact query (POST /hotels/{slug}/metrics/query, body from ?q=) and shows
   the total, the query itself and, on request, the same range by day,
   week or month, so a reader can check a figure without trusting us.
   The metric's own permission still applies on the server; a 403 or an
   unknown metric (a module that is switched off) shows as an explanation.
   ═══════════════════════════════════════════════════════════════════════ */

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, hotelPath, type ApiError } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, EmptyState, Segmented, Skeleton, Table, type Column } from "../../../../../ui";
import { metricHref, metricValue, parseMetricQuery } from "../lib";
import type { MetricInfo, MetricQueryBody, MetricResult } from "../types";

type Grain = NonNullable<MetricQueryBody["grain"]>;
type Row = MetricResult["rows"][number];

export default function MetricPage() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const router = useRouter();
  const params = useSearchParams();
  const raw = params?.get("q") ?? null;
  const query = parseMetricQuery(raw);
  const key = query ? JSON.stringify(query) : null;

  const [state, setState] = useState<{ key: string; data?: MetricResult; error?: ApiError } | null>(null);

  useEffect(() => {
    if (!key) return;
    let live = true;
    api.post<MetricResult>(hotelPath(slug, "/metrics/query"), JSON.parse(key))
      .then(data => { if (live) setState({ key, data }); })
      .catch((error: ApiError) => { if (live) setState({ key, error }); });
    return () => { live = false; };
  }, [slug, key]);

  if (!query) {
    return <Card><EmptyState title="No metric to show" body="Open this page from a figure in a pilot report." /></Card>;
  }

  const current = state?.key === key ? state : null;
  const res = current?.data;
  const info: MetricInfo | undefined = res
    ? { key: res.metric, label: res.label, unit: res.unit, currency: res.currency }
    : undefined;
  const setGrain = (g: Grain) => router.replace(metricHref(slug, { ...query, grain: g }), { scroll: false });

  const cols: Column<Row>[] = [
    { key: "bucket", header: "Period", render: r => r.bucket ?? "Total" },
    ...(res?.group_by ?? []).map(g => ({ key: `g-${g}`, header: g, render: (r: Row) => r.group[g] ?? "-" })),
    { key: "value", header: "Value", align: "right" as const, render: (r: Row) => metricValue(r.value, info, fmt) },
    ...(res?.compare ? [{ key: "cmp", header: "Compared", align: "right" as const, render: (r: Row) => metricValue(r.compare_value, info, fmt) }] : []),
  ];

  return (
    <>
      <Card
        title={res?.label ?? query.metric}
        subtitle={`${query.start} to ${query.end}, hotel-local days, both inclusive.`}
        actions={<Button size="sm" variant="ghost" onClick={() => router.back()}>Back</Button>}
      >
        {current?.error ? (
          <EmptyState
            title={current.error.code === "unknown_metric" ? "This metric is not available" : current.error.status === 403 ? "You cannot view this metric" : "The query failed"}
            body={current.error.code === "unknown_metric"
              ? "The module that provides it is switched off for this hotel, so the figure cannot be re-run here."
              : current.error.message}
          />
        ) : !res ? (
          <Skeleton lines={4} />
        ) : (
          <div className="cx-stack" style={{ gap: 14 }}>
            <div className="trs-hero__fig" style={{ maxWidth: 360 }}>
              <div className="trs-hero__label">Total</div>
              <div className="trs-hero__value">{metricValue(res.total, info, fmt)}</div>
              {res.compare && (
                <div className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>
                  {res.compare === "prev_year" ? "Same period last year" : "Previous period"}: {metricValue(res.compare_total, info, fmt)}
                </div>
              )}
            </div>
            <Segmented ariaLabel="Break down by" size="sm" value={query.grain ?? "total"} onChange={setGrain}
                       options={[{ value: "total", label: "Total" }, { value: "day", label: "By day" },
                                 { value: "week", label: "By week" }, { value: "month", label: "By month" }]} />
            {(query.grain ?? "total") !== "total" || res.group_by.length > 0 ? (
              <Table columns={cols} rows={res.rows} rowKey={(r, i) => `${r.bucket}:${i}`} caption="Metric rows" />
            ) : null}
            {res.truncated && <p className="trs-small" style={{ color: "var(--cx-warn)", margin: 0 }}>Showing the first 500 rows.</p>}
          </div>
        )}
      </Card>
      <Card title="The query" subtitle="Exactly what was sent to the metrics engine. The hotel filter is always added on the server.">
        <pre className="trs-json trs-mono" style={{ margin: 0 }}>{JSON.stringify(query, null, 2)}</pre>
      </Card>
    </>
  );
}
