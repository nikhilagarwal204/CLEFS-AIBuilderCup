"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Quiet hours, per person (brief quiet.py). Inside a person's window the
   scheduled brief and non-critical escalations wait until it ends; a
   critical item (a severity 4 signal such as a Service L1) still goes out,
   and "Send now" is a person asking, so it ignores the window. Times are
   the hotel's wall clock; a start later than the end runs overnight.
   The list is saved whole with the rest of the form (PUT /brief/settings).
   ═══════════════════════════════════════════════════════════════════════ */

import { Input } from "../../../../../ui";
import { roleLabel } from "../lib";
import type { QuietWindow } from "../types";

const DEFAULT_WINDOW = { start: "22:00", end: "07:00" };
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export interface QuietPerson { user_id: number; name: string; role: string }

export default function QuietHours({ people, value, onChange }: {
  people: readonly QuietPerson[];
  value: readonly QuietWindow[];
  onChange: (next: QuietWindow[]) => void;
}) {
  const byUser = new Map(value.map(w => [w.user_id, w] as const));
  const toggle = (id: number, on: boolean) =>
    onChange(on ? [...value.filter(w => w.user_id !== id), { user_id: id, ...DEFAULT_WINDOW }] : value.filter(w => w.user_id !== id));
  const edit = (id: number, key: "start" | "end", t: string) => {
    const hhmm = t.slice(0, 5);
    if (!HHMM.test(hhmm)) return;
    onChange(value.map(w => (w.user_id === id ? { ...w, [key]: hhmm } : w)));
  };

  return (
    <div className="brf-block">
      <span className="cx-label">Quiet hours</span>
      <p className="brf-muted" style={{ margin: 0 }}>
        The brief and reminders wait until the window ends. Critical items still go out.
      </p>
      <div className="brf-checks">
        {people.map(p => {
          const w = byUser.get(p.user_id);
          return (
            <div key={p.user_id} className="brf-order" style={{ flexWrap: "wrap", gap: 8 }}>
              <label className="brf-check brf-spacer">
                <input type="checkbox" checked={!!w} onChange={e => toggle(p.user_id, e.target.checked)} />
                <span>{p.name} <span className="brf-muted">{roleLabel(p.role)}</span></span>
              </label>
              {w && (
                <span className="brf-row" style={{ gap: 6 }}>
                  <Input type="time" step={60} aria-label={`${p.name}: quiet from`} value={w.start}
                         onChange={e => edit(p.user_id, "start", e.target.value)} style={{ width: 120 }} />
                  <span className="brf-muted">to</span>
                  <Input type="time" step={60} aria-label={`${p.name}: quiet until`} value={w.end}
                         onChange={e => edit(p.user_id, "end", e.target.value)} style={{ width: 120 }} />
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
