"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The ROI report document. Sections, in reading order: headline figures,
   success criteria, verified profit by module, revenue that ran through
   Clefs (shown beside verified profit, never added to it), service
   recovery, the conversion ROI, caveats, then numbered sources.
   Formatting uses the report's own currency (hotel.currency), not the
   console's, so a report opened from another hotel's context still reads
   in the right money.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo } from "react";
import { hotelPath, useApi } from "../../../../../../../lib/api";
import { makeFormatter, useFormat, type Formatter } from "../../../../../../../lib/format";
import { Badge, Card } from "../../../../../../../ui";
import { changePct, criterionTarget, dayLabel, metricValue, moduleLabel, plainNum, ratePct } from "../../../lib";
import type { MetricInfo, MetricQueryBody, PilotReport } from "../../../types";
import { Fig, SourceList, Sources, type Source } from "./Fig";

const RECOVERY_METRIC = "service.recovery_cost_aed";

export default function ReportBody({ slug, r }: { slug: string; r: PilotReport }) {
  const base = useFormat();
  const fmt = useMemo(() => makeFormatter(r.hotel.currency, base.tz, base.locale), [r.hotel.currency, base.tz, base.locale]);
  const metrics = useApi<MetricInfo[]>(hotelPath(slug, "/metrics"), [slug]);
  const info = useMemo(() => new Map((metrics.data ?? []).map(m => [m.key, m])), [metrics.data]);

  // ─── Sources (numbered in reading order) ──────────────────────────────
  const src = new Sources();
  const pilotRange = { start: r.scope.start_date, end: r.scope.reported_through };
  const vq = r.verified.query;
  const sVerified = src.cite(r.verified.label, vq);
  const sPerArn = src.cite(
    "Verified profit per available room-night", vq,
    `Verified profit divided by ${fmt.number(r.verified.available_room_nights)} available room-nights (occupancy_daily; the hotel's ${r.hotel.rooms_count} rooms for days without occupancy data).`,
  );
  const sRoi = src.cite(
    "ROI multiple", vq,
    "Verified profit per day over the pilot, times 365, divided by the annual price after the pilot.",
  );

  const money = (minor: number | null | undefined) => (minor == null ? "-" : fmt.money(minor, { minor: true }));

  return (
    <article className="trs-report" aria-label="Pilot ROI report">
      <header className="trs-report__head">
        <div className="trs-print-only trs-small">Clefs pilot report</div>
        <h1 className="trs-report__title">{r.pilot.name}: pilot results</h1>
        <div className="trs-report__meta">
          {r.hotel.name}{r.hotel.city ? `, ${r.hotel.city}` : ""} · {fmt.number(r.hotel.rooms_count)} rooms ·{" "}
          {r.scope.modules.map(moduleLabel).join(", ") || "No modules"}
        </div>
        <div className="trs-report__meta">
          Pilot {dayLabel(r.scope.start_date, fmt.locale)} to {dayLabel(r.scope.end_date, fmt.locale)}
          {r.scope.reported_through !== r.scope.end_date && `, reported through ${dayLabel(r.scope.reported_through, fmt.locale)}`}
          {" "}({fmt.number(r.scope.days)} days)
          {r.scope.baseline_start && ` · baseline ${dayLabel(r.scope.baseline_start, fmt.locale)} to ${dayLabel(r.scope.baseline_end, fmt.locale)}`}
        </div>
        <div className="cx-row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
          <Badge tone={r.final ? "gold" : "info"}>{r.final ? "Final" : "To date, still running"}</Badge>
          <Badge tone={r.all_met ? "ok" : "neutral"}>{r.all_met ? "Every success criterion met" : `Score ${ratePct(r.score)}`}</Badge>
          <span className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>Generated {fmt.dateTime(r.generated_at, "medium")}</span>
        </div>
      </header>

      <section className="trs-hero" aria-label="Headline figures">
        <div className="trs-hero__fig">
          <div className="trs-hero__label">{r.verified.label}</div>
          <div className="trs-hero__value"><Fig slug={slug} src={sVerified}>{money(r.verified.minor)}</Fig></div>
          <div className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>{fmt.number(r.verified.actions)} verified actions</div>
        </div>
        <div className="trs-hero__fig">
          <div className="trs-hero__label">Per available room-night</div>
          <div className="trs-hero__value"><Fig slug={slug} src={sPerArn}>{money(r.verified.per_available_room_night_minor)}</Fig></div>
          <div className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>
            {r.verified.per_room_minor != null ? `${money(r.verified.per_room_minor)} per room over the pilot` : " "}
          </div>
        </div>
        <div className="trs-hero__fig">
          <div className="trs-hero__label">Return on the price</div>
          <div className="trs-hero__value">
            <Fig slug={slug} src={sRoi}>{r.roi.roi_multiple != null ? `${plainNum(r.roi.roi_multiple)}x` : "-"}</Fig>
          </div>
          <div className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>
            {r.roi.payback_days != null ? `Pays back in about ${fmt.number(r.roi.payback_days)} days` : r.roi.price_minor == null ? "No price set" : " "}
          </div>
        </div>
      </section>

      <CriteriaSection slug={slug} r={r} fmt={fmt} info={info} src={src} />

      <Card title="Where the verified profit came from" as="section"
            subtitle="Only actions verified inside the pilot dates, from the Clefs ledger, net of reversals.">
        <table>
          <thead><tr><th>Module</th><th className="trs-num">Verified profit</th></tr></thead>
          <tbody>
            {Object.entries(r.verified.by_module).sort(([, a], [, b]) => (b.minor ?? 0) - (a.minor ?? 0)).map(([m, v]) => (
              <tr key={m}>
                <td>{moduleLabel(m)}</td>
                <td className="trs-num">
                  <Fig slug={slug} src={src.cite(`${r.verified.label}, ${moduleLabel(m)}`, withFilter(vq, "module", m))}>{money(v.minor)}</Fig>
                </td>
              </tr>
            ))}
            <tr>
              <td><strong>Total</strong></td>
              <td className="trs-num"><strong><Fig slug={slug} src={sVerified}>{money(r.verified.minor)}</Fig></strong></td>
            </tr>
          </tbody>
        </table>
      </Card>

      {r.revenue_through_clefs.length > 0 && (
        <Card title="Revenue that ran through Clefs" as="section"
              subtitle="Shown for context. It is not profit and is never added to the verified figure above.">
          <table>
            <tbody>
              {r.revenue_through_clefs.map(x => (
                <tr key={x.metric_key}>
                  <td>{x.label}</td>
                  <td className="trs-num">
                    <Fig slug={slug} src={src.cite(x.label, x.query)}>{metricValue(x.value, info.get(x.metric_key) ?? MONEY_INFO(fmt), fmt)}</Fig>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <RecoverySection slug={slug} r={r} money={money} src={src} pilotRange={pilotRange} />

      <Card title="Return on the price after the pilot" as="section">
        {r.roi.price_minor == null ? (
          <p style={{ margin: 0 }}>No conversion price was set for this pilot, so the return cannot be computed.</p>
        ) : (
          <table>
            <tbody>
              <tr><td>Price after the pilot</td><td className="trs-num">{money(r.roi.price_minor)} per {r.roi.billing_period === "year" ? "year" : "month"}</td></tr>
              <tr><td>Price per year</td><td className="trs-num">{money(r.roi.annual_price_minor)}</td></tr>
              <tr>
                <td>Verified profit at the pilot&apos;s run-rate, per year</td>
                <td className="trs-num">
                  <Fig slug={slug} src={src.cite("Annualised verified profit", vq, `Verified profit divided by ${r.scope.days} pilot days, times 365.`)}>
                    {money(r.roi.annualised_verified_minor)}
                  </Fig>
                </td>
              </tr>
              <tr><td>Return multiple</td><td className="trs-num"><Fig slug={slug} src={sRoi}>{r.roi.roi_multiple != null ? `${plainNum(r.roi.roi_multiple)}x` : "-"}</Fig></td></tr>
              <tr>
                <td>Payback</td>
                <td className="trs-num">
                  <Fig slug={slug} src={src.cite("Payback", vq, "Annual price divided by verified profit per pilot day.")}>
                    {r.roi.payback_days != null ? `${fmt.number(r.roi.payback_days)} days` : "-"}
                  </Fig>
                </td>
              </tr>
            </tbody>
          </table>
        )}
      </Card>

      {r.caveats.length > 0 && (
        <Card title="Read this with the figures" as="section">
          <ul className="trs-caveats">{r.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </Card>
      )}

      <Card title="Sources" as="section">
        <SourceList slug={slug} sources={src} />
      </Card>
    </article>
  );
}

// ─── Helpers ─────────────────────────────────────────────────────────────

/** The same query narrowed to one dimension value (by-module rows). */
function withFilter(q: MetricQueryBody, dim: string, value: string): MetricQueryBody {
  return { ...q, filters: { ...(q.filters ?? {}), [dim]: [value] } };
}

/** Fallback unit for the revenue rows when the viewer cannot list that metric. */
const MONEY_INFO = (fmt: Formatter): MetricInfo => ({ key: "", label: "", unit: "money", currency: fmt.currency });

const STATUS_LABEL = { met: "Met", not_met: "Not met", no_data: "No data" } as const;

// ─── Success criteria ────────────────────────────────────────────────────

function CriteriaSection({ slug, r, fmt, info, src }: {
  slug: string;
  r: PilotReport;
  fmt: Formatter;
  info: Map<string, MetricInfo>;
  src: Sources;
}) {
  // A final report scores the frozen outcome's period; a running one the
  // pilot start to today (reported_through).
  const start = (r.final && r.pilot.outcome?.period_start) || r.scope.start_date;
  const end = (r.final && r.pilot.outcome?.period_end) || r.scope.reported_through;
  const b0 = r.scope.baseline_start, b1 = r.scope.baseline_end;
  return (
    <Card title="Success criteria" as="section"
          subtitle={`Agreed before the pilot started. Score ${ratePct(r.score)} of weighted criteria met.`}>
      {r.criteria.length === 0 ? (
        <p style={{ margin: 0 }}>No success criteria were set for this pilot.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Criterion</th><th>Target</th><th className="trs-num">Baseline</th>
              <th className="trs-num">Result</th><th className="trs-num">Change</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {r.criteria.map((c, i) => {
              const mi = info.get(c.metric_key);
              const label = c.label || mi?.label || c.metric_key;
              const sBase: Source | undefined = b0 && b1 && c.baseline != null
                ? src.cite(`${label}, baseline`, { metric: c.metric_key, start: b0, end: b1, grain: "total" })
                : undefined;
              const sVal: Source | undefined = c.value != null
                ? src.cite(label, { metric: c.metric_key, start, end, grain: "total" })
                : undefined;
              return (
                <tr key={`${c.metric_key}:${i}`}>
                  <td>{label}</td>
                  <td>{criterionTarget(c, mi, fmt)}</td>
                  <td className="trs-num">{sBase ? <Fig slug={slug} src={sBase}>{metricValue(c.baseline, mi, fmt)}</Fig> : "-"}</td>
                  <td className="trs-num">{sVal ? <Fig slug={slug} src={sVal}>{metricValue(c.value, mi, fmt)}</Fig> : "-"}</td>
                  <td className="trs-num">{changePct(c.change_pct)}</td>
                  <td><Badge tone={c.status === "met" ? "ok" : c.status === "not_met" ? "bad" : "neutral"}>{STATUS_LABEL[c.status] ?? c.status}</Badge></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// ─── Service recovery ────────────────────────────────────────────────────

function RecoverySection({ slug, r, money, src, pilotRange }: {
  slug: string;
  r: PilotReport;
  money: (minor: number | null | undefined) => string;
  src: Sources;
  pilotRange: { start: string; end: string };
}) {
  const rec = r.recovery;
  if (rec.pilot_cost_minor == null && rec.baseline_cost_minor == null) return null;
  const b0 = r.scope.baseline_start, b1 = r.scope.baseline_end;
  const qPilot: MetricQueryBody = { metric: RECOVERY_METRIC, ...pilotRange, grain: "total" };
  const qBase: MetricQueryBody | null = b0 && b1 ? { metric: RECOVERY_METRIC, start: b0, end: b1, grain: "total" } : null;
  return (
    <Card title="Service recovery" as="section"
          subtitle="Compensation and goodwill gestures given to guests. Fewer needed means problems were caught earlier.">
      <table>
        <tbody>
          <tr>
            <td>Given during the pilot</td>
            <td className="trs-num"><Fig slug={slug} src={src.cite("Service recovery cost, pilot", qPilot)}>{money(rec.pilot_cost_minor)}</Fig></td>
          </tr>
          <tr>
            <td>Given during the baseline</td>
            <td className="trs-num">
              {qBase ? <Fig slug={slug} src={src.cite("Service recovery cost, baseline", qBase)}>{money(rec.baseline_cost_minor)}</Fig> : "No baseline set"}
            </td>
          </tr>
          {rec.avoided_minor != null && qBase && (
            <tr>
              <td>Avoided (baseline daily rate over the pilot days, less what was given)</td>
              <td className="trs-num">
                <Fig slug={slug} src={src.cite("Recovery cost avoided", qBase,
                  `Baseline cost per day times ${r.scope.days} pilot days, minus the pilot cost; never below zero. Not added to verified profit.`)}>
                  {money(rec.avoided_minor)}
                </Fig>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
  );
}
