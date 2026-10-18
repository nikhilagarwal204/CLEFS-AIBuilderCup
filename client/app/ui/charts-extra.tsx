"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Chart kit, part two: inline SVG/HTML, no chart library.

   Builds on app/app/charts.tsx (its palette, Tip, niceMax, axisTicks and
   useWidth) and follows the same palette rule stated in its header: plotted
   marks never reuse the raw brand tokens, they use hues stepped into the
   dark-mode lightness band (OKLCH L 0.48-0.67) and validated for
   colour-vision deficiency on the slate card surface (#0D141D):

   SERIES_COLORS  blue, gold, magenta, violet, green, in THIS order.
                  Worst adjacent CVD dE 11.1 (target >= 8), normal-vision
                  dE 19.4 (floor 15), all >= 3:1 on the surface. The first
                  two are the dashboard's existing progress/new hues, so a
                  series keeps its colour across both kits. Assigned in
                  order, never cycled: a sixth category folds into "Other".
   DIVERGING      blue (+) vs orange #D95926 (-), CVD dE 25.1, with a warm
                  grey midpoint. Blue/orange is the pair that survives
                  protanopia and deuteranopia; red/green would not.
   WATERFALL      increase blue, decrease orange, totals warm neutral
                  #B8B2A6 (dE >= 14.9 against both; gold failed next to
                  orange at dE 3.6, so it is not used here).
   HEAT_RAMP      one gold hue, monotone lightness, low end receding toward
                  the surface as a sequential scale should. Empty days are
                  hatched rather than coloured, so "no data" never reads
                  as "low".

   Identity is never colour alone: multi-series charts always carry a
   legend, values are in tooltips, and heatmap cells print their day.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  CHART_DONE, CHART_GRID, CHART_INK, CHART_INK_FAINT, CHART_NEW, CHART_PROGRESS, CHART_QUIET,
  Tip, axisTicks, niceMax, useWidth,
} from "../app/charts";
import { useFormat, toDate } from "../lib/format";
import { useHotelOptional } from "../lib/hotel";
import type { Timestamp } from "../lib/types";
import { Badge, type Tone } from "./core";

// ─── Palette ─────────────────────────────────────────────────────────────

export const SERIES_COLORS = [CHART_PROGRESS, CHART_NEW, "#D55181", "#9085E9", CHART_DONE] as const;
export const OTHER_COLOR = CHART_QUIET;
export const DIVERGING_POS = CHART_PROGRESS;
export const DIVERGING_NEG = "#D95926";
export const DIVERGING_MID = "rgba(242,238,230,0.28)";
export const WATERFALL_TOTAL = "#B8B2A6";
export const HEAT_RAMP = ["#463B26", "#5E4A26", "#86672D", "#B08736", "#DCAB4A"] as const;
const BAND_FILL = "rgba(242,238,230,0.07)";
const BAND_EDGE = "rgba(242,238,230,0.3)";
const SURFACE = "#0D141D";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const defaultFmt = (v: number) => compact.format(v);

/** Chart x values: epoch ms, ISO timestamps or YYYY-MM-DD calendar days. */
export type XValue = number | string;

function xToNum(x: XValue): number {
  if (typeof x === "number") return x;
  const d = toDate(x);
  return d ? d.getTime() : NaN;
}

function axisScale(minV: number, maxV: number) {
  const hi = niceMax(Math.max(maxV, 0) || 1);
  const lo = minV < 0 ? -niceMax(-minV) : 0;
  const ticks = lo < 0 ? [lo, 0, hi] : axisTicks(hi);
  return { lo, hi, ticks };
}

function Legend({ items }: { items: readonly { label: string; color: string; kind?: "box" | "line" | "band" }[] }) {
  if (items.length < 2) return null;
  return (
    <div className="cx-legend">
      {items.map(i => (
        <span key={i.label}>
          <i className={i.kind === "line" ? "cx-legend__line" : i.kind === "band" ? "cx-legend__band" : undefined}
             style={i.kind === "band" ? undefined : { background: i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

// ═════════════════════════════ LineChart ═══════════════════════════════

export interface LineSeries {
  key: string;
  label: string;
  /** Defaults to SERIES_COLORS by position. Pass one to keep an entity's colour stable across filters. */
  color?: string;
  points: { x: XValue; y: number | null }[];
  dashed?: boolean;
}

export type GuardrailBand =
  | { label?: string; low: number; high: number }
  | { label?: string; points: { x: XValue; low: number; high: number }[] };

export interface ChartEvent {
  x: XValue;
  label: string;
}

export function LineChart({
  series, height = 220, band, events = [], yFormat = defaultFmt, xFormat, ariaLabel, emptyNote = "Nothing to plot yet.",
}: {
  series: readonly LineSeries[];
  height?: number;
  /** Guardrail band (e.g. a rate floor/ceiling), constant or per-x. */
  band?: GuardrailBand;
  /** Vertical markers such as events or holidays. */
  events?: readonly ChartEvent[];
  yFormat?: (v: number) => string;
  xFormat?: (x: number) => string;
  ariaLabel: string;
  emptyNote?: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const fmt = useFormat();
  const fx = xFormat ?? ((x: number) => fmt.date(new Date(x)));

  const colored = series.map((s, i) => ({ ...s, color: s.color ?? SERIES_COLORS[i % SERIES_COLORS.length] }));
  const xs = useMemo(() => {
    const set = new Set<number>();
    for (const s of series) for (const p of s.points) { const n = xToNum(p.x); if (Number.isFinite(n)) set.add(n); }
    return Array.from(set).sort((a, b) => a - b);
  }, [series]);

  const ys: number[] = [];
  for (const s of series) for (const p of s.points) if (p.y !== null && Number.isFinite(p.y)) ys.push(p.y);
  if (band) {
    if ("points" in band) band.points.forEach(p => ys.push(p.low, p.high));
    else ys.push(band.low, band.high);
  }
  const has = ys.length > 0 && xs.length > 0;
  const { lo, hi, ticks } = axisScale(has ? Math.min(...ys) : 0, has ? Math.max(...ys) : 1);

  const padL = 44, padR = 12, padT = 18, padB = 26;
  const plotW = Math.max(0, w - padL - padR);
  const plotH = Math.max(0, height - padT - padB);
  const x0 = xs[0] ?? 0, x1 = xs[xs.length - 1] ?? 1;
  const sx = (x: number) => padL + (x1 === x0 ? plotW / 2 : ((x - x0) / (x1 - x0)) * plotW);
  const sy = (y: number) => padT + plotH - ((y - lo) / (hi - lo || 1)) * plotH;

  const pathFor = (pts: { x: XValue; y: number | null }[]) => {
    let d = "";
    let pen = false;
    for (const p of [...pts].sort((a, b) => xToNum(a.x) - xToNum(b.x))) {
      const xn = xToNum(p.x);
      if (p.y === null || !Number.isFinite(p.y) || !Number.isFinite(xn)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${sx(xn).toFixed(1)},${sy(p.y).toFixed(1)}`;
      pen = true;
    }
    return d;
  };

  let bandPath = "";
  if (band && has) {
    if ("points" in band) {
      const pts = [...band.points].map(p => ({ x: xToNum(p.x), low: p.low, high: p.high }))
        .filter(p => Number.isFinite(p.x)).sort((a, b) => a.x - b.x);
      if (pts.length) {
        bandPath = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x)},${sy(p.high)}`).join("")
          + [...pts].reverse().map(p => `L${sx(p.x)},${sy(p.low)}`).join("") + "Z";
      }
    } else {
      bandPath = `M${padL},${sy(band.high)}H${padL + plotW}V${sy(band.low)}H${padL}Z`;
    }
  }

  const tickXs = xs.length <= 1 ? xs : [xs[0], xs[Math.floor((xs.length - 1) / 2)], xs[xs.length - 1]];
  const hx = hover !== null ? xs[hover] : null;

  const onMove = (clientX: number, rect: DOMRect) => {
    if (!xs.length) return;
    const px = clientX - rect.left;
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(sx(xs[i]) - px) < Math.abs(sx(xs[best]) - px)) best = i;
    setHover(best);
  };

  const legendItems = [
    ...colored.map(s => ({ label: s.label, color: s.color, kind: "line" as const })),
    ...(band?.label ? [{ label: band.label, color: BAND_FILL, kind: "band" as const }] : []),
  ];

  return (
    <div ref={ref} className="cx-chart" role="group" aria-label={ariaLabel}>
      {!has && <div className="chart-empty" style={{ height }}>{emptyNote}</div>}
      {has && w > 0 && (
        <svg width={w} height={height}
             onMouseMove={e => onMove(e.clientX, e.currentTarget.getBoundingClientRect())}
             onMouseLeave={() => setHover(null)}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={padL} x2={padL + plotW} y1={sy(t)} y2={sy(t)} stroke={t === 0 && lo < 0 ? "rgba(255,255,255,0.16)" : CHART_GRID} strokeWidth={1} />
              <text x={padL - 8} y={sy(t) + 3.5} textAnchor="end" fontSize={10} fill={CHART_INK_FAINT} style={{ fontVariantNumeric: "tabular-nums" }}>
                {yFormat(t)}
              </text>
            </g>
          ))}

          {bandPath && (
            <path d={bandPath} fill={BAND_FILL} stroke={BAND_EDGE} strokeDasharray="4 4" strokeWidth={1} />
          )}

          {events.map(ev => {
            const xn = xToNum(ev.x);
            if (!Number.isFinite(xn) || xn < x0 || xn > x1) return null;
            const ex = sx(xn);
            return (
              <g key={`${ev.label}-${xn}`}>
                <line x1={ex} x2={ex} y1={padT} y2={padT + plotH} stroke="rgba(242,238,230,0.22)" strokeWidth={1} strokeDasharray="2 3" />
                <circle cx={ex} cy={padT - 4} r={3} fill={CHART_INK}>
                  <title>{`${fx(xn)}: ${ev.label}`}</title>
                </circle>
              </g>
            );
          })}

          {colored.map(s => (
            <path key={s.key} d={pathFor(s.points)} fill="none" stroke={s.color} strokeWidth={2}
                  strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "5 4" : undefined} />
          ))}

          {tickXs.map((t, i) => (
            <text key={t} x={sx(t)} y={padT + plotH + 17} fontSize={10} fill={CHART_INK_FAINT}
                  textAnchor={tickXs.length > 1 && i === 0 ? "start" : i === tickXs.length - 1 && tickXs.length > 1 ? "end" : "middle"}>
              {fx(t)}
            </text>
          ))}

          {hx !== null && (
            <g pointerEvents="none">
              <line x1={sx(hx)} x2={sx(hx)} y1={padT} y2={padT + plotH} stroke="rgba(242,238,230,0.28)" strokeWidth={1} />
              {colored.map(s => {
                const p = s.points.find(q => xToNum(q.x) === hx);
                if (!p || p.y === null) return null;
                return <circle key={s.key} cx={sx(hx)} cy={sy(p.y)} r={4} fill={s.color} stroke={SURFACE} strokeWidth={2} />;
              })}
            </g>
          )}
          <line x1={padL} x2={padL + plotW} y1={padT + plotH} y2={padT + plotH} stroke="rgba(255,255,255,0.1)" strokeWidth={1} />
        </svg>
      )}
      {hx !== null && (
        <Tip
          x={Math.min(Math.max(sx(hx), 90), Math.max(90, w - 90))}
          y={padT + 4}
          below
          label={fx(hx) + (events.find(e => xToNum(e.x) === hx) ? ` · ${events.find(e => xToNum(e.x) === hx)!.label}` : "")}
          rows={[
            ...colored.map(s => {
              const p = s.points.find(q => xToNum(q.x) === hx);
              return { label: s.label, value: p && p.y !== null ? yFormat(p.y) : "-", color: s.color };
            }),
            ...(band && "low" in band ? [{ label: band.label ?? "Guardrail", value: `${yFormat(band.low)} to ${yFormat(band.high)}` }] : []),
            ...(band && "points" in band
              ? (() => {
                  const b = band.points.find(p => xToNum(p.x) === hx);
                  return b ? [{ label: band.label ?? "Guardrail", value: `${yFormat(b.low)} to ${yFormat(b.high)}` }] : [];
                })()
              : []),
          ]}
        />
      )}
      <Legend items={legendItems} />
    </div>
  );
}

// ═════════════════════════════ Sparkline ═══════════════════════════════

export function Sparkline({
  values, width = 96, height = 28, color = CHART_PROGRESS, ariaLabel, area = true,
}: {
  values: readonly (number | null)[];
  width?: number;
  height?: number;
  color?: string;
  ariaLabel?: string;
  area?: boolean;
}) {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (nums.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const min = Math.min(...nums), max = Math.max(...nums);
  const pad = 3;
  const sx = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const sy = (v: number) => pad + (1 - (v - min) / (max - min || 1)) * (height - pad * 2);
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) { pen = false; return; }
    d += `${pen ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`;
    pen = true;
  });
  const lastIdx = values.map((v, i) => (v === null ? -1 : i)).filter(i => i >= 0).pop() ?? 0;
  const firstIdx = values.findIndex(v => v !== null);
  const areaD = area && firstIdx >= 0
    ? `${d}L${sx(lastIdx)},${height - pad}L${sx(firstIdx)},${height - pad}Z`
    : "";
  return (
    <svg width={width} height={height} role={ariaLabel ? "img" : undefined} aria-label={ariaLabel} aria-hidden={ariaLabel ? undefined : true}>
      {areaD && <path d={areaD} fill={color} opacity={0.12} />}
      <path d={d} fill="none" stroke={color} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={sx(lastIdx)} cy={sy(values[lastIdx] as number)} r={2.5} fill={color} />
    </svg>
  );
}

// ═══════════════════════════ CalendarHeatmap ═══════════════════════════

export interface HeatDay {
  /** YYYY-MM-DD (hotel-local calendar day). */
  date: string;
  value: number | null;
  /** Extra line for the tooltip, e.g. an event name. */
  note?: string;
}

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function isoWeekday(key: string): number {
  const d = new Date(`${key}T00:00:00Z`).getUTCDay();
  return d === 0 ? 7 : d;
}

function addDays(key: string, n: number): string {
  const d = new Date(`${key}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function CalendarHeatmap({
  days, range = 30, start, weekendDays, selected, onSelect, valueFormat = defaultFmt,
  ariaLabel, lowLabel = "Low", highLabel = "High", max,
}: {
  days: readonly HeatDay[];
  /** 30, 60 or 90 days from `start`. */
  range?: 30 | 60 | 90;
  /** First day shown (YYYY-MM-DD). Defaults to the earliest day in `days`. */
  start?: string;
  /** ISO weekdays to shade (1 = Mon ... 7 = Sun). Defaults to the hotel's weekend_days. */
  weekendDays?: readonly number[];
  selected?: string | null;
  onSelect?: (date: string) => void;
  valueFormat?: (v: number) => string;
  ariaLabel: string;
  lowLabel?: string;
  highLabel?: string;
  /** Fix the top of the scale (e.g. 100 for an index); defaults to the data max. */
  max?: number;
}) {
  const hotel = useHotelOptional()?.hotel;
  const fmt = useFormat();
  const weekend = new Set(weekendDays ?? hotel?.weekend_days ?? [6, 7]);
  const byDate = useMemo(() => new Map(days.map(d => [d.date, d])), [days]);
  const [todayKey] = useState(() => fmt.dayKey(Date.now()));
  const first = start ?? (days.length ? [...days].map(d => d.date).sort()[0] : todayKey);
  const keys = Array.from({ length: range }, (_, i) => addDays(first, i));
  const top = max ?? Math.max(0, ...days.map(d => d.value ?? 0));
  const bucket = (v: number) => Math.min(HEAT_RAMP.length - 1, Math.max(0, Math.floor((v / (top || 1)) * HEAT_RAMP.length - 1e-9)));
  const lead = isoWeekday(first) - 1;
  const [hover, setHover] = useState<{ key: string; x: number; y: number } | null>(null);

  return (
    <div className="cx-chart" role="group" aria-label={ariaLabel}>
      <div className="cx-heat">
        {DOW.map((d, i) => (
          <div key={d} className="cx-heat__dow" data-weekend={weekend.has(i + 1) || undefined}>{d}</div>
        ))}
        {Array.from({ length: lead }, (_, i) => <div key={`pad-${i}`} className="cx-heat__pad" aria-hidden="true" />)}
        {keys.map(key => {
          const day = byDate.get(key);
          const v = day?.value ?? null;
          const empty = v === null || v === undefined;
          const label = `${fmt.date(key, "medium")}: ${empty ? "no data" : valueFormat(v)}${day?.note ? `, ${day.note}` : ""}`;
          return (
            <button
              key={key}
              type="button"
              className="cx-heat__cell"
              data-weekend={weekend.has(isoWeekday(key)) || undefined}
              data-empty={empty || undefined}
              aria-pressed={selected === key}
              aria-label={label}
              style={{ background: empty ? "rgba(255,255,255,0.03)" : HEAT_RAMP[bucket(v)], color: !empty && bucket(v) >= 3 ? "#16202D" : undefined }}
              onClick={() => onSelect?.(key)}
              onMouseEnter={e => setHover({ key, x: e.currentTarget.offsetLeft + e.currentTarget.offsetWidth / 2, y: e.currentTarget.offsetTop })}
              onMouseLeave={() => setHover(null)}
              onFocus={e => setHover({ key, x: e.currentTarget.offsetLeft + e.currentTarget.offsetWidth / 2, y: e.currentTarget.offsetTop })}
              onBlur={() => setHover(null)}
            >
              {Number(key.slice(8))}
            </button>
          );
        })}
      </div>
      {hover && (() => {
        const day = byDate.get(hover.key);
        const rows = [{ label: "Value", value: day?.value !== null && day?.value !== undefined ? valueFormat(day.value) : "No data" }];
        if (day?.note) rows.push({ label: day.note, value: "" });
        return <Tip x={hover.x} y={hover.y} label={fmt.date(hover.key, "medium")} rows={rows} />;
      })()}
      <div className="cx-heat__scale" aria-hidden="true">
        <span>{lowLabel}</span>
        {HEAT_RAMP.map(c => <i key={c} style={{ background: c }} />)}
        <span>{highLabel}</span>
        <span style={{ marginLeft: 10 }}>
          <i style={{ background: "rgba(255,255,255,0.03)", boxShadow: "inset 0 0 0 1px rgba(225,175,104,0.28)", verticalAlign: "middle" }} /> Weekend
        </span>
      </div>
    </div>
  );
}

// ═════════════════════════════ DivergingBar ════════════════════════════

export function DivergingBar({
  rows, format = defaultFmt, ariaLabel, positiveLabel = "Above", negativeLabel = "Below", emptyNote = "Nothing to compare yet.",
}: {
  rows: readonly { label: string; value: number; note?: string }[];
  format?: (v: number) => string;
  ariaLabel: string;
  positiveLabel?: string;
  negativeLabel?: string;
  emptyNote?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(0, ...rows.map(r => Math.abs(r.value)));
  if (!rows.length || max === 0) return <div className="chart-empty" style={{ height: 120 }}>{emptyNote}</div>;
  return (
    <div className="cx-chart" role="group" aria-label={ariaLabel} style={{ marginTop: 10 }}>
      {rows.map((r, i) => {
        const pct = (Math.abs(r.value) / max) * 50;
        const pos = r.value >= 0;
        return (
          <div key={r.label} className="hbar-row" title={`${r.label}: ${format(r.value)}${r.note ? ` (${r.note})` : ""}`}
               onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
               style={{ opacity: hover === null || hover === i ? 1 : 0.6, transition: "opacity 0.15s" }}>
            <div className="hbar-row__label">{r.label}</div>
            <div className="hbar-row__track">
              <div style={{ position: "absolute", left: "50%", top: -3, bottom: -3, width: 1, background: DIVERGING_MID }} />
              {r.value !== 0 && (
                <div className="hbar-row__seg" style={{
                  left: pos ? "calc(50% + 1px)" : `calc(${50 - pct}% - 1px)`,
                  width: `${pct}%`,
                  background: pos ? DIVERGING_POS : DIVERGING_NEG,
                  borderRadius: pos ? "0 4px 4px 0" : "4px 0 0 4px",
                }} />
              )}
            </div>
            <div className="hbar-row__total" style={{ width: 64 }}>{format(r.value)}</div>
          </div>
        );
      })}
      <div className="cx-legend">
        <span><i style={{ background: DIVERGING_POS }} />{positiveLabel}</span>
        <span><i style={{ background: DIVERGING_NEG }} />{negativeLabel}</span>
      </div>
    </div>
  );
}

// ═══════════════════════════════ Donut ═════════════════════════════════

export interface DonutSlice {
  label: string;
  value: number;
  color?: string;
}

/** Top `maxSlices` by value plus one "Other"; colours follow input order, not rank. */
export function foldSlices(slices: readonly DonutSlice[], maxSlices = 5): (DonutSlice & { color: string })[] {
  const positive = slices.map((s, i) => ({ ...s, i })).filter(s => s.value > 0);
  const keep = new Set([...positive].sort((a, b) => b.value - a.value).slice(0, maxSlices).map(s => s.i));
  const kept = positive.filter(s => keep.has(s.i));
  const rest = positive.filter(s => !keep.has(s.i)).reduce((a, s) => a + s.value, 0);
  const out = kept.map((s, k) => ({ label: s.label, value: s.value, color: s.color ?? SERIES_COLORS[k % SERIES_COLORS.length] }));
  if (rest > 0) out.push({ label: "Other", value: rest, color: OTHER_COLOR });
  return out;
}

export function Donut({
  slices, maxSlices = 5, size = 160, thickness = 22, format = defaultFmt, centerLabel, centerValue, ariaLabel,
  emptyNote = "Nothing to plot yet.",
}: {
  slices: readonly DonutSlice[];
  maxSlices?: number;
  size?: number;
  thickness?: number;
  format?: (v: number) => string;
  centerLabel?: ReactNode;
  centerValue?: ReactNode;
  ariaLabel: string;
  emptyNote?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const data = foldSlices(slices, Math.min(maxSlices, 5));
  const total = data.reduce((a, s) => a + s.value, 0);
  if (total <= 0) return <div className="chart-empty" style={{ height: size }}>{emptyNote}</div>;

  const r = size / 2 - 2;
  const ri = r - thickness;
  const c = size / 2;
  const starts = data.reduce<number[]>((acc, s, k) => {
    acc.push(k === 0 ? -Math.PI / 2 : acc[k - 1] + (data[k - 1].value / total) * Math.PI * 2);
    return acc;
  }, []);
  const arcs = data.map((s, i) => {
    const sweep = (s.value / total) * Math.PI * 2;
    const a0 = starts[i], a1 = starts[i] + sweep;
    const large = sweep > Math.PI ? 1 : 0;
    const p = (rad: number, a: number) => `${c + rad * Math.cos(a)},${c + rad * Math.sin(a)}`;
    const d = data.length === 1
      ? `M${c - r},${c}A${r},${r} 0 1 1 ${c + r},${c}A${r},${r} 0 1 1 ${c - r},${c}M${c - ri},${c}A${ri},${ri} 0 1 0 ${c + ri},${c}A${ri},${ri} 0 1 0 ${c - ri},${c}Z`
      : `M${p(r, a0)}A${r},${r} 0 ${large} 1 ${p(r, a1)}L${p(ri, a1)}A${ri},${ri} 0 ${large} 0 ${p(ri, a0)}Z`;
    return { ...s, d, i };
  });
  const focus = hover !== null ? data[hover] : null;

  return (
    <div className="cx-chart" role="group" aria-label={ariaLabel}
         style={{ display: "flex", gap: 18, alignItems: "center", flexWrap: "wrap" }}>
      <div style={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
        <svg width={size} height={size}>
          {arcs.map(a => (
            <path key={a.label} d={a.d} fill={a.color} fillRule="evenodd"
                  stroke={SURFACE} strokeWidth={2}
                  opacity={hover === null || hover === a.i ? 1 : 0.5}
                  onMouseEnter={() => setHover(a.i)} onMouseLeave={() => setHover(null)}>
              <title>{`${a.label}: ${format(a.value)} (${Math.round((a.value / total) * 100)}%)`}</title>
            </path>
          ))}
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", pointerEvents: "none", textAlign: "center", padding: thickness + 4 }}>
          <div style={{ fontSize: "1.05rem", fontWeight: 600, color: "var(--clefs-ink)", fontVariantNumeric: "tabular-nums" }}>
            {focus ? format(focus.value) : centerValue ?? format(total)}
          </div>
          <div style={{ fontSize: "0.7rem", color: "var(--clefs-ink-faint)", marginTop: 3 }}>
            {focus ? focus.label : centerLabel ?? "Total"}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7, minWidth: 140, flex: 1 }}>
        {data.map((s, i) => (
          <div key={s.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
               style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.8rem", color: "var(--clefs-ink-muted)", opacity: hover === null || hover === i ? 1 : 0.6 }}>
            <i style={{ width: 9, height: 9, borderRadius: 2, background: s.color, flexShrink: 0 }} />
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.label}</span>
            <b style={{ color: "var(--clefs-ink)", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{format(s.value)}</b>
            <span style={{ width: 38, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{Math.round((s.value / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════ WaterfallChart ════════════════════════════

export interface WaterfallStep {
  label: string;
  value: number;
  /** "total" bars are anchored at zero and show the running figure (e.g. gross, net). */
  kind?: "delta" | "total";
}

export function WaterfallChart({
  steps, height = 220, format = defaultFmt, ariaLabel, emptyNote = "Nothing to plot yet.",
}: {
  steps: readonly WaterfallStep[];
  height?: number;
  format?: (v: number) => string;
  ariaLabel: string;
  emptyNote?: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  // Running position: a "total" bar resets it, a delta moves it.
  const bars = steps.reduce<(WaterfallStep & { from: number; to: number })[]>((acc, s) => {
    const run = acc.length ? acc[acc.length - 1].to : 0;
    acc.push(s.kind === "total" ? { ...s, from: 0, to: s.value } : { ...s, from: run, to: run + s.value });
    return acc;
  }, []);
  const vals = bars.flatMap(b => [b.from, b.to]);
  if (!bars.length) return <div className="chart-empty" style={{ height }}>{emptyNote}</div>;
  const { lo, hi, ticks } = axisScale(Math.min(0, ...vals), Math.max(0, ...vals));

  const padL = 48, padR = 8, padT = 22, padB = 28;
  const plotW = Math.max(0, w - padL - padR);
  const plotH = Math.max(0, height - padT - padB);
  const band = plotW / bars.length;
  const barW = Math.max(6, Math.min(44, band - 12));
  const sy = (v: number) => padT + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const colorOf = (b: (typeof bars)[number]) => (b.kind === "total" ? WATERFALL_TOTAL : b.value >= 0 ? DIVERGING_POS : DIVERGING_NEG);

  return (
    <div ref={ref} className="cx-chart" role="group" aria-label={ariaLabel}>
      {w > 0 && (
        <svg width={w} height={height}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={padL} x2={padL + plotW} y1={sy(t)} y2={sy(t)} stroke={CHART_GRID} strokeWidth={1} />
              <text x={padL - 8} y={sy(t) + 3.5} textAnchor="end" fontSize={10} fill={CHART_INK_FAINT}>{format(t)}</text>
            </g>
          ))}
          {bars.map((b, i) => {
            const x = padL + i * band + (band - barW) / 2;
            const yTop = sy(Math.max(b.from, b.to));
            const h = Math.max(1, Math.abs(sy(b.from) - sy(b.to)));
            const next = bars[i + 1];
            return (
              <g key={`${b.label}-${i}`} opacity={hover === null || hover === i ? 1 : 0.55}
                 onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={x} y={yTop} width={barW} height={h} rx={3} fill={colorOf(b)} />
                {next && next.kind !== "total" && (
                  <line x1={x + barW} x2={x + band} y1={sy(b.to)} y2={sy(b.to)} stroke="rgba(242,238,230,0.25)" strokeDasharray="2 2" />
                )}
                <text x={x + barW / 2} y={yTop - 6} textAnchor="middle" fontSize={10} fontWeight={600} fill={CHART_INK}>
                  {b.kind === "total" ? format(b.value) : `${b.value >= 0 ? "+" : ""}${format(b.value)}`}
                </text>
                <text x={x + barW / 2} y={padT + plotH + 16} textAnchor="middle" fontSize={10} fill={CHART_INK_FAINT}>
                  {b.label.length > 12 && band < 80 ? `${b.label.slice(0, 11)}…` : b.label}
                </text>
                <rect x={padL + i * band} y={padT} width={band} height={plotH} fill="transparent" />
              </g>
            );
          })}
          <line x1={padL} x2={padL + plotW} y1={sy(0)} y2={sy(0)} stroke="rgba(255,255,255,0.16)" strokeWidth={1} />
        </svg>
      )}
      {hover !== null && bars[hover] && (
        <Tip x={Math.min(Math.max(padL + hover * band + band / 2, 80), Math.max(80, w - 80))}
             y={sy(Math.max(bars[hover].from, bars[hover].to))}
             below={sy(Math.max(bars[hover].from, bars[hover].to)) < 60}
             label={bars[hover].label}
             rows={[
               { label: bars[hover].kind === "total" ? "Total" : "Change", value: format(bars[hover].value), color: colorOf(bars[hover]) },
               ...(bars[hover].kind === "total" ? [] : [{ label: "Running", value: format(bars[hover].to) }]),
             ]} />
      )}
      <div className="cx-legend">
        <span><i style={{ background: DIVERGING_POS }} />Increase</span>
        <span><i style={{ background: DIVERGING_NEG }} />Decrease</span>
        <span><i style={{ background: WATERFALL_TOTAL }} />Total</span>
      </div>
    </div>
  );
}

// ═════════════════════════════ CountdownChip ═══════════════════════════

/** "2d 4h left" / "Overdue 3h". Ticks every 30 s; tone escalates as the deadline nears. */
export function CountdownChip({
  due, warnWithinHours = 24, prefix, doneLabel,
}: {
  due: Timestamp | null | undefined;
  warnWithinHours?: number;
  prefix?: string;
  /** Show this instead of a countdown (e.g. "Recovered"). */
  doneLabel?: string;
}) {
  const fmt = useFormat();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  if (doneLabel) return <Badge tone="ok">{doneLabel}</Badge>;
  const d = toDate(due ?? null);
  if (!d) return null;
  const mins = (d.getTime() - now) / 60_000;
  const overdue = mins < 0;
  const tone: Tone = overdue ? "bad" : mins <= warnWithinHours * 60 ? "warn" : "neutral";
  const text = overdue ? `Overdue ${fmt.mins(-mins)}` : `${fmt.mins(mins)} left`;
  return (
    <Badge tone={tone} dot title={`Due ${fmt.dateTime(d, "medium")}`} className="cx-countdown">
      {prefix ? `${prefix} ` : ""}{text}
    </Badge>
  );
}
