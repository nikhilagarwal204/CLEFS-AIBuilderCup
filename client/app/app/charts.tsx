"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Chart kit - inline SVG/HTML, no chart library.

   Palette note: the marks do NOT reuse the raw brand tokens. --hc-gold and
   friends sit above the dark-mode lightness band (OKLCH L 0.48–0.67), so on
   the slate ground one series would shout over the others. These are the same
   hues stepped into the band, then validated for colour-blind separation in
   the order they touch inside a stack (new ↔ progress ↔ completed). Badges and
   text keep the brand tokens; only plotted marks use these.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState, type ReactNode } from "react";

export const CHART_NEW = "#BA8A2E";
export const CHART_PROGRESS = "#5997E2";
export const CHART_DONE = "#2EAF6C";
export const CHART_QUIET = "rgba(242,238,230,0.17)";
export const CHART_GRID = "rgba(255,255,255,0.06)";
export const CHART_INK = "rgba(242,238,230,0.74)";
export const CHART_INK_FAINT = "rgba(242,238,230,0.36)";

/** Legend order = the order segments read top-to-bottom in a stack. */
export const STATUS_SERIES = [
  { key: "new", label: "New", color: CHART_NEW },
  { key: "progress", label: "In progress", color: CHART_PROGRESS },
  { key: "completed", label: "Completed", color: CHART_DONE },
] as const;

export const chartStyles = `
  .chart-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--clefs-line-soft);
    border-radius: 16px;
    padding: 20px 22px 16px;
    min-width: 0;
    transition: border-color 0.25s ease;
  }
  .chart-card:hover { border-color: rgba(225,175,104,0.18); }
  .chart-card__title { font-size: 0.95rem; font-weight: 600; color: #f2eee6; letter-spacing: -0.01em; }
  .chart-card__hint  { font-size: 0.76rem; color: rgba(242,238,230,0.4); margin-top: 4px; line-height: 1.45; }
  .chart-card__head  { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
  .chart-legend { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
  .chart-legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 0.74rem; color: rgba(242,238,230,0.5); }
  .chart-legend i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
  .chart-empty { display: flex; align-items: center; justify-content: center; text-align: center;
                 color: rgba(242,238,230,0.28); font-size: 0.83rem; padding: 0 12px; }

  /* Bars carry the colour; the tip carries the numbers. */
  .chart-tip {
    position: absolute; z-index: 40; pointer-events: none;
    background: rgba(8,13,22,0.97);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 10px; padding: 9px 12px;
    box-shadow: 0 14px 34px rgba(0,0,0,0.55);
    font-size: 0.76rem; white-space: nowrap;
    transform: translate(-50%, -100%);
  }
  .chart-tip__label { color: #f2eee6; font-weight: 600; margin-bottom: 6px; font-size: 0.78rem; }
  .chart-tip__row { display: flex; align-items: center; gap: 7px; color: rgba(242,238,230,0.62); line-height: 1.7; }
  .chart-tip__row b { color: #f2eee6; font-weight: 600; margin-left: auto; padding-left: 14px; font-variant-numeric: tabular-nums; }
  .chart-tip__row i { width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }

  .hbar-row { display: flex; align-items: center; gap: 12px; padding: 7px 0; }
  .hbar-row__label {
    width: 116px; flex-shrink: 0; font-size: 0.8rem; color: rgba(242,238,230,0.66);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .hbar-row__track { position: relative; flex: 1; height: 14px; min-width: 0; }
  .hbar-row__seg { position: absolute; top: 0; bottom: 0; border-radius: 2px; }
  .hbar-row__seg:last-child { border-radius: 2px 4px 4px 2px; }
  .hbar-row__seg:only-child { border-radius: 2px 4px 4px 2px; }
  .hbar-row__total {
    width: 42px; flex-shrink: 0; text-align: right; font-size: 0.82rem; font-weight: 600;
    color: #f2eee6; font-variant-numeric: tabular-nums;
  }
  .hbar-row__note { font-size: 0.74rem; color: rgba(242,238,230,0.34); font-variant-numeric: tabular-nums; }

  @media (max-width: 520px) {
    .chart-card { padding: 16px 14px 12px; }
    .hbar-row__label { width: 88px; font-size: 0.75rem; }
  }
`;

/* ── Measure the container so the SVG can be laid out in real pixels ────── */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.getBoundingClientRect().width);
    const ro = new ResizeObserver(entries => setW(entries[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/* ── Shared chrome ──────────────────────────────────────────────────────── */
export function ChartCard({
  title, hint, legend, aside, children,
}: {
  title: string;
  hint?: string;
  legend?: readonly { label: string; color: string }[];
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="chart-card">
      <div className="chart-card__head">
        <div style={{ minWidth: 0 }}>
          <div className="chart-card__title">{title}</div>
          {hint && <div className="chart-card__hint">{hint}</div>}
        </div>
        {aside}
      </div>
      {legend && legend.length > 1 && (
        <div className="chart-legend">
          {legend.map(l => (
            <span key={l.label}><i style={{ background: l.color }} />{l.label}</span>
          ))}
        </div>
      )}
      {children}
    </div>
  );
}

export function Tip({ x, y, label, rows, below = false }: {
  x: number; y: number; label: string;
  rows: { label: string; value: string; color?: string }[];
  /** Tall bars leave no room above the mark - drop the tip under the cap instead. */
  below?: boolean;
}) {
  return (
    <div className="chart-tip"
         style={{ left: x, top: below ? y + 16 : y - 10, transform: below ? "translate(-50%, 0)" : undefined }}>
      <div className="chart-tip__label">{label}</div>
      {rows.map(r => (
        <div key={r.label} className="chart-tip__row">
          {r.color && <i style={{ background: r.color }} />}
          {r.label}<b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

/* Rounded data-end, square at the baseline. */
function barPath(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}L${x},${y + rr}Q${x},${y} ${x + rr},${y}L${x + w - rr},${y}Q${x + w},${y} ${x + w},${y + rr}L${x + w},${y + h}Z`;
}

/* Round the axis up to a whole step that still halves cleanly, so the plot
   keeps a little headroom without stranding the bars in the bottom third. */
export function niceMax(v: number) {
  if (v <= 5) return Math.max(1, Math.ceil(v));
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const step = (mag / 5) * 2;               // 4, 40, 400 …
  return Math.ceil(v / step) * step;
}

export function axisTicks(max: number) {
  if (max <= 5) return Array.from({ length: max + 1 }, (_, i) => i);
  return [0, max / 2, max];
}

/* ═══════════════════ Column chart (single or stacked) ═══════════════════ */
export interface ColumnDatum {
  /** Full name, used by the tooltip. */
  label: string;
  /** Short axis tick. Omit to leave the slot unlabelled. */
  tick?: string;
  values: number[];
}

const GAP = 2; // the surface gap that separates touching segments

export function ColumnChart({
  data, series, height = 180, emphasis, highlightIndex, divider,
  capLabels = "auto", unit = "", valueFormat, ariaLabel, emptyNote = "Nothing to plot yet.",
}: {
  data: ColumnDatum[];
  /** Legend order, read top-to-bottom in the stack. Rendered bottom-up. */
  series: readonly { label: string; color: string }[];
  height?: number;
  /** Per-column colour override - the emphasis form, for single-series charts. */
  emphasis?: (d: ColumnDatum, i: number) => string | undefined;
  highlightIndex?: number;
  divider?: { afterIndex: number; label: string };
  /** "auto" = cap every column when there are few, else only the peak. */
  capLabels?: "auto" | "peak" | "none";
  unit?: string;
  /** Formats every value label (cap, axis, tooltip, title), e.g. hotel money;
      without it the raw number plus `unit` is printed. */
  valueFormat?: (v: number) => string;
  ariaLabel: string;
  emptyNote?: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const fmtV = (v: number) => (valueFormat ? valueFormat(v) : `${v}${unit}`);

  const totals = data.map(d => d.values.reduce((a, b) => a + b, 0));
  const grand = totals.reduce((a, b) => a + b, 0);
  const max = niceMax(Math.max(...totals, 1));
  const ticks = axisTicks(max);
  const peak = totals.indexOf(Math.max(...totals));
  const mode = capLabels === "auto" ? (data.length <= 8 ? "all" : "peak") : capLabels;

  const padL = 32, padR = 10, padT = 20, padB = 24;
  const plotW = Math.max(0, w - padL - padR);
  const plotH = Math.max(0, height - padT - padB);
  const base = padT + plotH;
  const band = data.length ? plotW / data.length : 0;
  const barW = Math.max(3, Math.min(24, band - (band > 22 ? 10 : band > 10 ? 5 : 3)));
  const scale = plotH / max;

  return (
    <div ref={ref} style={{ position: "relative", marginTop: 12 }} role="group" aria-label={ariaLabel}>
      {grand === 0 && (
        <div className="chart-empty" style={{ height }}>{emptyNote}</div>
      )}
      {grand > 0 && w > 0 && (
        <svg width={w} height={height} style={{ display: "block", overflow: "visible" }}>
          {/* Recessive grid - solid hairlines, one shade off the surface */}
          {ticks.map(t => {
            const y = base - t * scale;
            return (
              <g key={t}>
                <line x1={padL} x2={padL + plotW} y1={y} y2={y} stroke={CHART_GRID} strokeWidth={1} />
                <text x={padL - 8} y={y + 3.5} textAnchor="end" fontSize={10} fill={CHART_INK_FAINT}
                      style={{ fontVariantNumeric: "tabular-nums" }}>
                  {valueFormat ? valueFormat(t) : Number.isInteger(t) ? t : t.toFixed(1)}
                </text>
              </g>
            );
          })}

          {divider && divider.afterIndex >= 0 && divider.afterIndex < data.length - 1 && (() => {
            const dx = padL + (divider.afterIndex + 1) * band;
            const flip = dx > padL + plotW * 0.6;   // keep the label inside the plot
            return (
              <g>
                <line x1={dx} x2={dx} y1={padT - 6} y2={base}
                      stroke="rgba(242,238,230,0.22)" strokeWidth={1} />
                <text x={dx + (flip ? -6 : 6)} y={padT - 8} textAnchor={flip ? "end" : "start"}
                      fontSize={10} fill={CHART_INK_FAINT}>{divider.label}</text>
              </g>
            );
          })()}

          {data.map((d, i) => {
            const x = padL + i * band + (band - barW) / 2;
            const total = totals[i];
            let cum = 0;
            // Stack bottom-up: the last series in legend order sits on the baseline.
            const stack = series.map((s, si) => ({ s, si, v: d.values[si] || 0 })).reverse();
            const topIdx = stack.reduce((acc, seg, k) => (seg.v > 0 ? k : acc), -1);
            return (
              <g key={i} opacity={hover === null || hover === i ? 1 : 0.55}
                 style={{ transition: "opacity 0.15s" }}>
                {stack.map((seg, k) => {
                  if (seg.v <= 0) return null;
                  const yTop = base - (cum + seg.v) * scale;
                  const yBot = base - cum * scale;
                  cum += seg.v;
                  const h = Math.max(1, yBot - yTop - (k > 0 ? GAP : 0));
                  const fill = emphasis?.(d, i) ?? seg.s.color;
                  return (
                    <path key={seg.s.label} d={barPath(x, yTop, barW, h, k === topIdx ? 4 : 0)} fill={fill}>
                      <title>{`${d.label} · ${seg.s.label}: ${fmtV(seg.v)}`}</title>
                    </path>
                  );
                })}

                {/* Value on the cap */}
                {total > 0 && (mode === "all" || (mode === "peak" && i === peak)) && (
                  <text x={x + barW / 2} y={base - total * scale - 7} textAnchor="middle"
                        fontSize={10.5} fontWeight={600} fill={CHART_INK}>
                    {fmtV(total)}
                  </text>
                )}

                {/* Axis tick */}
                {d.tick && (
                  <text x={x + barW / 2} y={base + 15} textAnchor="middle" fontSize={10}
                        fill={i === highlightIndex ? CHART_INK : CHART_INK_FAINT}
                        fontWeight={i === highlightIndex ? 600 : 400}>
                    {d.tick}
                  </text>
                )}

                {/* Hit strip - the whole band, so there are no dead zones */}
                <rect x={padL + i * band} y={padT - 10} width={band} height={plotH + 12}
                      fill="transparent" style={{ cursor: "default" }}
                      onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
              </g>
            );
          })}

          <line x1={padL} x2={padL + plotW} y1={base} y2={base} stroke="rgba(255,255,255,0.1)" strokeWidth={1} />
        </svg>
      )}

      {hover !== null && data[hover] && totals[hover] >= 0 && (
        <Tip
          x={Math.min(Math.max(padL + hover * band + band / 2, 80), Math.max(80, w - 80))}
          y={padT + plotH - totals[hover] * scale}
          below={padT + plotH - totals[hover] * scale < 26 + series.length * 22}
          label={data[hover].label}
          rows={[
            ...(series.length > 1
              ? series.map((s, si) => ({
                label: s.label,
                value: valueFormat ? valueFormat(data[hover].values[si] || 0) : String(data[hover].values[si] || 0),
                color: s.color,
              }))
              : []),
            { label: series.length > 1 ? "Total" : series[0].label, value: fmtV(totals[hover]) },
          ]}
        />
      )}
    </div>
  );
}

/* ═══════════════ Horizontal bars (single or stacked) ═══════════════════ */
export interface BarRow {
  label: string;
  values: number[];
  /** Small muted figure shown after the total, e.g. an average. */
  note?: string;
}

export function HBarChart({
  rows, series, unit = "", valueFormat, ariaLabel, emptyNote = "Nothing to plot yet.",
}: {
  rows: BarRow[];
  series: readonly { label: string; color: string }[];
  unit?: string;
  /** As ColumnChart's: formats the row totals and segment titles. */
  valueFormat?: (v: number) => string;
  ariaLabel: string;
  emptyNote?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const fmtV = (v: number) => (valueFormat ? valueFormat(v) : `${v}${unit}`);
  const totals = rows.map(r => r.values.reduce((a, b) => a + b, 0));
  const max = Math.max(...totals, 1);
  const any = totals.some(t => t > 0);

  if (!any) return <div className="chart-empty" style={{ height: 120 }}>{emptyNote}</div>;

  return (
    <div style={{ marginTop: 14, position: "relative" }} role="group" aria-label={ariaLabel}>
      {rows.map((r, i) => {
        const total = totals[i];
        let cum = 0;
        return (
          <div key={r.label} className="hbar-row"
               onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
               style={{ opacity: hover === null || hover === i ? 1 : 0.6, transition: "opacity 0.15s" }}>
            <div className="hbar-row__label" title={r.label}>{r.label}</div>
            <div className="hbar-row__track">
              {series.map((s, si) => {
                const v = r.values[si] || 0;
                if (v <= 0) return null;
                const left = (cum / max) * 100;
                const width = (v / max) * 100;
                cum += v;
                return (
                  <div key={s.label} className="hbar-row__seg"
                       title={`${r.label} · ${s.label}: ${fmtV(v)}`}
                       style={{
                         left: `${left}%`,
                         // the 2px surface gap that separates touching segments
                         width: `calc(${width}% - ${series.length > 1 ? GAP : 0}px)`,
                         background: s.color,
                       }} />
                );
              })}
            </div>
            <div className="hbar-row__total">{fmtV(total)}</div>
            {r.note !== undefined && <div className="hbar-row__note" style={{ width: 62, textAlign: "right" }}>{r.note}</div>}
          </div>
        );
      })}
    </div>
  );
}

/* ═════════════════════════ Inline share meter ══════════════════════════ */
export function ShareMeter({ value, max, color, title }: { value: number; max: number; color: string; title: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div title={title} style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.06)", overflow: "hidden", marginTop: 7 }}>
      <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 2, transition: "width 0.4s ease" }} />
    </div>
  );
}
