"use client";

/* Opening-hours controls. Times are hotel-local "HH:MM" (the server buckets
   in the hotel timezone, Canon 3), so the browser clock never enters into
   it. A window ending before it starts runs past midnight, which is how a
   23:00-02:00 late-night menu is written; we say so next to the row so
   nobody "fixes" it into two windows. */

import { Button, Input } from "../../../../ui";
import { ALL_DAYS, HHMM, WEEKDAYS, maskHas, toggleDay } from "./lib";
import type { HoursWindow } from "./types";

// ─── Day picker ──────────────────────────────────────────────────────────

export function DayPicker({ mask, onChange, label = "Days" }: {
  mask: number;
  onChange: (mask: number) => void;
  label?: string;
}) {
  return (
    <div className="ups-chips" role="group" aria-label={label}>
      {WEEKDAYS.map((d, i) => (
        <button key={d} type="button" className="ups-chip" aria-pressed={maskHas(mask, i)}
                onClick={() => onChange(toggleDay(mask, i))}>
          {d}
        </button>
      ))}
    </div>
  );
}

// ─── Windows ─────────────────────────────────────────────────────────────

export function hoursValid(hours: HoursWindow[]): boolean {
  return hours.every(h => HHMM.test(h.from) && HHMM.test(h.to) && h.from !== h.to && h.days_mask > 0);
}

export default function HoursEditor({ value, onChange }: {
  value: HoursWindow[];
  onChange: (next: HoursWindow[]) => void;
}) {
  const set = (i: number, patch: Partial<HoursWindow>) =>
    onChange(value.map((w, j) => (j === i ? { ...w, ...patch } : w)));

  return (
    <div className="cx-stack" style={{ gap: 8 }}>
      {value.length === 0 && (
        <div className="ups-sub">No windows: the outlet is treated as open around the clock.</div>
      )}
      {value.map((w, i) => {
        const bad = !HHMM.test(w.from) || !HHMM.test(w.to) || w.from === w.to;
        return (
          <div key={i} className="ups-window">
            <div className="ups-window__row">
              <Input type="time" aria-label="Opens" value={w.from} invalid={!HHMM.test(w.from)}
                     onChange={e => set(i, { from: e.target.value })} />
              <span className="cx-faint">to</span>
              <Input type="time" aria-label="Closes" value={w.to} invalid={!HHMM.test(w.to)}
                     onChange={e => set(i, { to: e.target.value })} />
              <Button size="sm" variant="ghost" onClick={() => onChange(value.filter((_, j) => j !== i))}>
                Remove
              </Button>
            </div>
            <DayPicker mask={w.days_mask} onChange={m => set(i, { days_mask: m })} />
            {!bad && w.to < w.from && <div className="ups-sub">Runs past midnight, counted on the day it opens.</div>}
            {w.days_mask === 0 && <div className="cx-error-text">Pick at least one day.</div>}
          </div>
        );
      })}
      <div>
        <Button size="sm" onClick={() => onChange([...value, { from: "07:00", to: "23:00", days_mask: ALL_DAYS }])}>
          Add opening window
        </Button>
      </div>
    </div>
  );
}
