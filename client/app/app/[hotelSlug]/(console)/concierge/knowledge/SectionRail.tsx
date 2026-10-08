"use client";

/* The section list beside the editor. A red dot marks a required section
   with no approved answer (the go-live check fails on those), an amber dot
   a section with answers waiting for approval. On a phone the rail becomes
   a horizontal strip so the answers stay on the first screen. */

import type { KbSection } from "../types";

export function SectionRail({ sections, missing, value, onChange }: {
  sections: KbSection[];
  missing: string[];
  value: number | null;
  onChange: (id: number | null) => void;
}) {
  const totalPending = sections.reduce((n, s) => n + s.pending, 0);
  return (
    <nav className="cc-rail" aria-label="Knowledge sections">
      <button type="button" className="cc-rail__btn" aria-current={value === null} onClick={() => onChange(null)}>
        <span>All answers</span>
        {totalPending > 0 && <span className="cc-rail__meta"><span className="cc-dot cc-dot--warn" />{totalPending}</span>}
      </button>
      {sections.map(s => {
        const gap = missing.includes(s.key);
        return (
          <button key={s.id} type="button" className="cc-rail__btn" aria-current={value === s.id} onClick={() => onChange(s.id)}
            title={gap ? "Required: no approved answer yet" : s.pending ? `${s.pending} waiting for approval` : undefined}>
            <span>{s.title_en}{s.required ? " *" : ""}</span>
            <span className="cc-rail__meta">
              {gap && <span className="cc-dot cc-dot--bad" aria-label="missing" />}
              {s.pending > 0 && <><span className="cc-dot cc-dot--warn" />{s.pending}</>}
              {s.approved > 0 && <span>{s.approved}</span>}
            </span>
          </button>
        );
      })}
      <span className="cc-small cx-faint" style={{ padding: "6px 10px" }}>* required before go-live</span>
    </nav>
  );
}
