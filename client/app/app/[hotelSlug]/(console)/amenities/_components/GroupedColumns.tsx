"use client";

/* Grouped column chart: requested vs issued vs expected per item, side by
   side (02 spec 5.3 overview). The kit's ColumnChart only stacks, and a
   stack would add requested on top of expected, which is already inside
   expected (E = basis x std + requested). So this draws adjacent bars per
   group, on the same chart tokens (grid, ink, tooltip) as the kit. */

import { useState } from "react";
import { axisTicks, niceMax, Tip, useWidth } from "../../../../charts";

export interface GroupDatum {
  label: string;
  /** Axis tick; cut to fit the column width (the full label is in the tooltip). */
  tick?: string;
  values: number[];
}

const PAD_L = 36;
const PAD_B = 22;
const PAD_T = 8;

export default function GroupedColumns({
  data, series, height = 220, ariaLabel, format = (v: number) => String(Math.round(v)), emptyNote = "Nothing to plot yet.",
}: {
  data: readonly GroupDatum[];
  series: readonly { label: string; color: string }[];
  height?: number;
  ariaLabel: string;
  format?: (v: number) => string;
  emptyNote?: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const peak = Math.max(0, ...data.flatMap(d => d.values));
  if (!data.length || peak <= 0) return <div className="chart-empty" style={{ height: 140 }}>{emptyNote}</div>;

  const max = niceMax(peak);
  const ticks = axisTicks(max);
  const plotW = Math.max(0, w - PAD_L);
  const plotH = height - PAD_B - PAD_T;
  const groupW = data.length ? plotW / data.length : 0;
  const inner = Math.max(6, groupW * 0.72);
  const barW = Math.max(3, inner / series.length - 2);
  const y = (v: number) => PAD_T + plotH - (Math.max(0, v) / max) * plotH;
  const hovered = hover === null ? null : data[hover];
  // About 6.5px per character at 11px: cut ticks so neighbours never collide
  // on a phone, where eight groups share roughly 300px.
  const maxChars = Math.max(3, Math.floor(groupW / 6.5));
  const tickOf = (d: GroupDatum) => {
    const t = d.tick ?? d.label;
    return t.length > maxChars ? `${t.slice(0, maxChars - 1)}\u2026` : t;
  };

  // role="img" sits on the svg alone: ARIA makes an img's children
  // presentational, so on the wrapper it would hide the data table and the
  // legend below from screen readers.
  return (
    <div ref={ref} className="amn-gcol">
      {w > 0 && (
        <svg height={height} viewBox={`0 0 ${w} ${height}`} role="img" aria-label={ariaLabel}>
          {ticks.map(t => (
            <g key={t}>
              <line className="amn-gcol__grid" x1={PAD_L} x2={w} y1={y(t)} y2={y(t)} />
              <text className="amn-gcol__tick" x={PAD_L - 6} y={y(t) + 4} textAnchor="end">{format(t)}</text>
            </g>
          ))}
          {data.map((d, gi) => {
            const gx = PAD_L + gi * groupW + (groupW - inner) / 2;
            return (
              <g key={d.label} opacity={hover === null || hover === gi ? 1 : 0.55} className="amn-gcol__bar">
                {series.map((s, si) => {
                  const v = d.values[si] || 0;
                  const top = y(v);
                  return (
                    <rect key={s.label} x={gx + si * (barW + 2)} y={top} width={barW}
                          height={Math.max(0, PAD_T + plotH - top)} rx={Math.min(3, barW / 2)} fill={s.color} />
                  );
                })}
                <text className="amn-gcol__tick" x={PAD_L + gi * groupW + groupW / 2} y={height - 6} textAnchor="middle">
                  {tickOf(d)}
                </text>
                <rect className="amn-gcol__hit" x={PAD_L + gi * groupW} y={PAD_T} width={groupW} height={plotH}
                      onMouseEnter={() => setHover(gi)} onMouseLeave={() => setHover(null)} />
              </g>
            );
          })}
        </svg>
      )}
      {/* The hover tip repeats the table below for pointer users only. */}
      {hovered && hover !== null && (
        <Tip x={PAD_L + hover * groupW + groupW / 2} y={y(Math.max(...hovered.values))} label={hovered.label}
             rows={series.map((s, i) => ({ label: s.label, value: format(hovered.values[i] || 0), color: s.color }))} />
      )}
      <div className="cx-legend">
        {series.map(s => <span key={s.label}><i style={{ background: s.color }} />{s.label}</span>)}
      </div>
      {/* Screen readers get the numbers, not the picture. */}
      <div className="amn-sr">
        <table>
          <caption>{ariaLabel}</caption>
          <thead><tr><th>Item</th>{series.map(s => <th key={s.label}>{s.label}</th>)}</tr></thead>
          <tbody>
            {data.map(d => (
              <tr key={d.label}><th>{d.label}</th>{series.map((s, i) => <td key={s.label}>{format(d.values[i] || 0)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
