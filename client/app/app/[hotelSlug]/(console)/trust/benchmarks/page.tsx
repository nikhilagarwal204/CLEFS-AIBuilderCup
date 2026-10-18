"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Benchmarks: where this hotel sits against similar opted-in hotels, per
   metric and month. Opt-in only, both ways: a hotel sees cohort positions
   only after it shares its own, and opting out deletes its positions at
   once. The server publishes nothing below 3 hotels and hides the median
   below 5, so small cohorts show a band (top, middle, bottom third) only.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Table, useToast, type Column, type Tone } from "../../../../../ui";
import { errText, metricValue, trustApi } from "../lib";
import type { BenchmarkItem, Benchmarks, MetricInfo } from "../types";

const BAND: Record<NonNullable<BenchmarkItem["band"]>, { label: string; tone: Tone }> = {
  top: { label: "Top third", tone: "ok" },
  middle: { label: "Middle third", tone: "neutral" },
  bottom: { label: "Bottom third", tone: "warn" },
};

/** Last complete month in the hotel's timezone, as YYYY-MM. */
function lastMonth(todayKey: string): string {
  const [y, m] = todayKey.split("-").map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
}

/** "2026-09" as "Sep 2026". */
function monthLabel(p: string, locale: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  if (!m) return p;
  try {
    return new Intl.DateTimeFormat(locale, { month: "short", year: "numeric", timeZone: "UTC" })
      .format(new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1)));
  } catch {
    return p;
  }
}

export default function BenchmarksPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const allowed = can("trust.pilots.view");
  const canOpt = can("trust.manage");
  // Empty = every published month (newest first); a month narrows it.
  const [period, setPeriod] = useState("");
  const res = useApi<Benchmarks>(
    allowed ? `${trustApi(slug, "/benchmarks")}${/^\d{4}-\d{2}$/.test(period) ? `?period=${period}` : ""}` : null,
    [slug, period],
  );
  const [busy, setBusy] = useState(false);

  const groups = useMemo(() => {
    const by = new Map<string, BenchmarkItem[]>();
    for (const it of res.data?.items ?? []) {
      // Cohort and month together: positions from different months never share a table.
      const k = `${it.cohort_label} · ${monthLabel(it.period, fmt.locale)}`;
      const list = by.get(k) ?? [];
      list.push(it);
      by.set(k, list);
    }
    return [...by.entries()];
  }, [res.data, fmt.locale]);

  if (!allowed) return <Card><EmptyState title="Benchmarks are for owners, GMs and finance" /></Card>;

  async function setOptIn(next: boolean) {
    if (!next && !window.confirm("Stop sharing? This hotel's benchmark positions are deleted now, and you stop seeing cohorts.")) return;
    setBusy(true);
    try {
      await api.put(trustApi(slug, "/benchmarks/opt-in"), { opted_in: next });
      toast.success(next ? "Sharing turned on" : "Sharing turned off",
        next ? "Your positions appear after the next monthly snapshot." : "Your positions were deleted.");
      res.reload();
    } catch (e) {
      toast.error("Could not change sharing", errText(e));
    } finally {
      setBusy(false);
    }
  }

  const optedIn = res.data?.opted_in ?? false;
  const cols: Column<BenchmarkItem>[] = [
    { key: "metric", header: "Metric", render: i => i.metric_label },
    { key: "band", header: "Your position", render: i => (i.band ? <Badge tone={BAND[i.band].tone}>{BAND[i.band].label}</Badge> : "-") },
    {
      key: "p50", header: "Cohort median", align: "right",
      render: i => (i.p50 == null ? <span className="trs-small" title="Hidden below 5 hotels">Hidden</span>
        : metricValue(i.p50, { key: i.metric_key, label: i.metric_label, unit: i.unit } as MetricInfo, fmt)),
    },
    { key: "n", header: "Hotels", align: "right", hideOnMobile: true, render: i => fmt.number(i.n_hotels) },
    { key: "at", header: "Computed", hideOnMobile: true, render: i => fmt.date(i.computed_at, "medium") },
  ];

  return (
    <>
      <Card
        title="Benchmarks"
        subtitle="Compare with similar hotels that also share. Only bands and medians are shown, never another hotel's figures or name."
        actions={canOpt && res.data ? (
          <Button size="sm" variant={optedIn ? "ghost" : "primary"} loading={busy} onClick={() => setOptIn(!optedIn)}>
            {optedIn ? "Stop sharing" : "Share and compare"}
          </Button>
        ) : undefined}
      >
        <div className="trs-filters" style={{ marginBottom: 12 }}>
          <Field label="Month" htmlFor="bm-period">
            <Input id="bm-period" type="month" value={period} max={lastMonth(fmt.dayKey(new Date()))} onChange={e => setPeriod(e.target.value)} />
          </Field>
          {period && (
            <div style={{ alignSelf: "end" }}><Button size="sm" variant="ghost" onClick={() => setPeriod("")}>All months</Button></div>
          )}
        </div>
        {res.error ? (
          <EmptyState title="Could not load benchmarks" body={res.error.message} />
        ) : res.data && !optedIn ? (
          <EmptyState
            title="This hotel is not sharing benchmarks"
            body={canOpt
              ? "Share to see where you stand. Your figures are pooled anonymously with at least two other hotels before anything is published."
              : "The owner can turn sharing on from this page."}
          />
        ) : (
          <div className="cx-stack" style={{ gap: 16 }}>
            {res.loading && !res.data && <Table columns={cols} rows={[]} rowKey={i => i.metric_key} loading />}
            {res.data && groups.length === 0 && (
              <EmptyState title={period ? "Nothing published for this month" : "Nothing published yet"} body="A cohort needs at least 3 sharing hotels with data before it is published." />
            )}
            {groups.map(([label, items]) => (
              <div key={label}>
                <div className="trs-section-title" style={{ marginTop: 0 }}>
                  {label}{items.some(i => i.bands_only) ? " (bands only, fewer than 5 hotels)" : ""}
                </div>
                <Table columns={cols} rows={items} rowKey={i => `${i.cohort_key}:${i.metric_key}`} caption={`Benchmarks for ${label}`} />
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
