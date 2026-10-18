"use client";

/* ═══════════════════════════════════════════════════════════════════════
   AdjustSlider: picks a rate inside [floor, ceiling] and nowhere else.
   The range input walks model.ratePositions by index (floor, each multiple
   of 5, ceiling), so arrow keys always move one step and an odd floor stays
   reachable. The typed box snaps on blur with the same clamp, so neither
   control can produce the server's 400 rate_out_of_bounds.
   ═══════════════════════════════════════════════════════════════════════ */

import { useId, useMemo, useState } from "react";
import { useFormat } from "../../../../../lib/format";
import { Input } from "../../../../../ui";
import { changeFromBar, clampRate, nearestIndex, num, ratePositions, signedPct } from "../_lib/model";

export default function AdjustSlider({
  floor, ceiling, value, bar, proposed, onChange, disabled = false, currency,
}: {
  floor: number;
  ceiling: number;
  value: number;
  /** Current BAR, for the "vs BAR" readout. */
  bar: string | null;
  /** The engine's proposal, marked on the ends row. */
  proposed: number | null;
  onChange: (v: number) => void;
  disabled?: boolean;
  currency?: string;
}) {
  const fmt = useFormat();
  const id = useId();
  const positions = useMemo(() => ratePositions(floor, ceiling), [floor, ceiling]);
  const [draft, setDraft] = useState<string | null>(null);
  const money = (v: number) => fmt.money(v, { maximumFractionDigits: 0, currency });
  const vsBar = changeFromBar(value, bar);

  if (!positions.length) return null;
  const idx = nearestIndex(positions, value);

  const commitDraft = () => {
    if (draft === null) return;
    const n = num(draft.replace(/[^\d.]/g, ""));
    if (n !== null) onChange(clampRate(n, floor, ceiling));
    setDraft(null);
  };

  return (
    <div className="dr-slider">
      <label htmlFor={`${id}-range`} className="cx-sr">Rate for this night</label>
      <input
        id={`${id}-range`}
        type="range"
        min={0}
        max={positions.length - 1}
        step={1}
        value={idx}
        disabled={disabled || positions.length < 2}
        aria-valuetext={money(positions[idx])}
        onChange={e => onChange(positions[Number(e.target.value)] ?? value)}
      />
      <div className="dr-slider__ends" aria-hidden="true">
        <span>Floor {money(floor)}</span>
        {proposed !== null && <span>Suggested {money(proposed)}</span>}
        <span>Ceiling {money(ceiling)}</span>
      </div>
      <div className="dr-slider__value">
        <label htmlFor={`${id}-box`} className="dr-muted">Rate</label>
        <Input
          id={`${id}-box`}
          inputMode="decimal"
          value={draft ?? String(value)}
          disabled={disabled}
          onChange={e => setDraft(e.target.value)}
          onBlur={commitDraft}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); commitDraft(); } }}
          aria-describedby={`${id}-vs`}
        />
        <span id={`${id}-vs`} className="dr-muted">
          {vsBar === null ? "No BAR to compare" : `${signedPct(vsBar)} vs your BAR`}
        </span>
      </div>
    </div>
  );
}
