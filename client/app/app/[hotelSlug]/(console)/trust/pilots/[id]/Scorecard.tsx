"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Pilot scorecard: one row per success criterion with its baseline, the
   value so far, the change and met / not met / no data. "No data" is its
   own state on purpose: a metric with nothing recorded yet is not a fail,
   and the owner should see the difference. Values link to /trust/metric,
   which re-runs the exact query over the scorecard period.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useFormat } from "../../../../../../lib/format";
import { Badge, Card, EmptyState, Sparkline, Table, type Column, type Tone } from "../../../../../../ui";
import { changePct, criterionTarget, dayLabel, metricHref, metricValue, ratePct } from "../../lib";
import type { CriterionEval, MetricInfo, PilotCheckpoint, PilotDetail } from "../../types";

const STATUS: Record<CriterionEval["status"], { label: string; tone: Tone }> = {
  met: { label: "Met", tone: "ok" },
  not_met: { label: "Not met", tone: "bad" },
  no_data: { label: "No data yet", tone: "neutral" },
};

export default function Scorecard({ slug, pilot, info }: {
  slug: string;
  pilot: PilotDetail;
  info: Map<string, MetricInfo>;
}) {
  const fmt = useFormat();
  const sc = pilot.scorecard;
  const frozen = pilot.status === "completed" || pilot.status === "converted";

  const cols: Column<CriterionEval>[] = [
    {
      key: "criterion", header: "Criterion",
      render: c => (
        <div>
          <div>{c.label || info.get(c.metric_key)?.label || c.metric_key}</div>
          <div className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>{criterionTarget(c, info.get(c.metric_key), fmt)}</div>
        </div>
      ),
    },
    {
      key: "baseline", header: "Baseline", align: "right", hideOnMobile: true,
      render: c => (c.baseline != null && pilot.baseline_start && pilot.baseline_end
        ? <Link className="trs-fig" href={metricHref(slug, { metric: c.metric_key, start: pilot.baseline_start, end: pilot.baseline_end })}>
            {metricValue(c.baseline, info.get(c.metric_key), fmt)}
          </Link>
        : "-"),
    },
    {
      key: "value", header: frozen ? "Final" : "So far", align: "right",
      render: c => (c.value != null && sc
        ? <Link className="trs-fig" href={metricHref(slug, { metric: c.metric_key, start: sc.period_start, end: sc.period_end })}>
            {metricValue(c.value, info.get(c.metric_key), fmt)}
          </Link>
        : "-"),
    },
    { key: "change", header: "Change", align: "right", hideOnMobile: true, render: c => changePct(c.change_pct) },
    { key: "weight", header: "Weight", align: "right", hideOnMobile: true, render: c => String(Number(c.weight)) },
    { key: "status", header: "", render: c => <Badge tone={STATUS[c.status]?.tone ?? "neutral"}>{STATUS[c.status]?.label ?? c.status}</Badge> },
  ];

  return (
    <>
      <Card
        title={frozen ? "Final scorecard" : "Scorecard to date"}
        subtitle={sc
          ? `${dayLabel(sc.period_start, fmt.locale)} to ${dayLabel(sc.period_end, fmt.locale)}. Score ${ratePct(sc.score)} of weighted criteria met${sc.all_met ? ", every criterion met" : ""}.`
          : "The scorecard appears once the pilot has started."}
      >
        {sc ? (
          <Table columns={cols} rows={sc.criteria} rowKey={(c, i) => `${c.metric_key}:${i}`} caption="Success criteria"
                 empty={<EmptyState title="No success criteria" body="Edit the pilot to add what success means." />} />
        ) : (
          <EmptyState title="Not started" body="Start the pilot to begin scoring." />
        )}
      </Card>
      <Checkpoints items={pilot.checkpoints} />
    </>
  );
}

// ─── Checkpoints ─────────────────────────────────────────────────────────

function Checkpoints({ items }: { items: PilotCheckpoint[] }) {
  const fmt = useFormat();
  if (!items.length) return null;
  const sorted = [...items].sort((a, b) => a.date.localeCompare(b.date));
  const scores = sorted.map(c => (c.score == null ? null : Number(c.score) * 100));
  const latest = sorted[sorted.length - 1];
  return (
    <Card title="Checkpoints" subtitle="Scored every morning at 07:00. Two off-track checkpoints in a row raise a signal for the owner.">
      <div className="cx-row" style={{ gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 220px", minWidth: 0 }}>
          <Sparkline values={scores} width={240} height={40} ariaLabel="Pilot score by day" />
        </div>
        <div className="trs-small">
          Latest {dayLabel(latest.date, fmt.locale)}: {ratePct(latest.score)}{" "}
          {latest.on_track === false ? <Badge tone="warn">Off track</Badge> : latest.on_track ? <Badge tone="ok">On track</Badge> : null}
        </div>
      </div>
    </Card>
  );
}
