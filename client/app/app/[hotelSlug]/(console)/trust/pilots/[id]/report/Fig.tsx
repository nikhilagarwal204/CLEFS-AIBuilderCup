"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Report figures and their sources. Every number in the ROI report is a
   <Fig> that links to /trust/metric (the metric query that produced it)
   and carries a footnote number, because a link is invisible on paper:
   the printed "Sources" list names the metric key and dates for each note.
   Derived figures (per room-night, ROI multiple, payback) cite the query
   they derive from plus a note with the formula.
   Sources dedupe by query, so the same metric and dates share one note.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import type { ReactNode } from "react";
import { metricHref } from "../../../lib";
import type { MetricQueryBody } from "../../../types";

export interface Source {
  n: number;
  label: string;
  query: MetricQueryBody | null;
  note: string | null;
}

/** Collects numbered sources while the report renders. Built fresh on each
    render (the report is static), so numbering follows reading order. */
export class Sources {
  readonly list: Source[] = [];
  private byKey = new Map<string, number>();

  add(label: string, query: MetricQueryBody | null, note: string | null = null): number {
    const key = JSON.stringify([query, note]);
    const hit = this.byKey.get(key);
    if (hit) return hit;
    const n = this.list.length + 1;
    this.list.push({ n, label, query, note });
    this.byKey.set(key, n);
    return n;
  }

  get(n: number): Source | undefined {
    return this.list[n - 1];
  }

  /** add() then get() in one call, for figures rendered inline. */
  cite(label: string, query: MetricQueryBody | null, note: string | null = null): Source | undefined {
    return this.get(this.add(label, query, note));
  }
}

/** One figure: the value linked to its query, with its footnote number. */
export function Fig({ slug, src, children }: { slug: string; src: Source | undefined; children: ReactNode }) {
  if (!src) return <>{children}</>;
  const title = src.query
    ? `${src.query.metric}, ${src.query.start} to ${src.query.end}${src.note ? `. ${src.note}` : ""}`
    : src.note ?? src.label;
  return (
    <>
      {src.query
        ? <Link className="trs-fig" href={metricHref(slug, src.query)} title={title}>{children}</Link>
        : <span title={title}>{children}</span>}
      <a className="trs-fn" href={`#src-${src.n}`} aria-label={`Source ${src.n}`}>{src.n}</a>
    </>
  );
}

/** The numbered sources list at the foot of the report. */
export function SourceList({ slug, sources }: { slug: string; sources: Sources }) {
  if (!sources.list.length) return null;
  return (
    <ol className="trs-sources">
      {sources.list.map(s => (
        <li key={s.n} id={`src-${s.n}`}>
          <strong>{s.label}.</strong>{" "}
          {s.query && (
            <>
              Metric <span className="trs-mono">{s.query.metric}</span>, {s.query.start} to {s.query.end}
              {s.query.filters && Object.keys(s.query.filters).length > 0 &&
                `, ${Object.entries(s.query.filters).map(([k, v]) => `${k} = ${v.join(" or ")}`).join("; ")}`}
              .{" "}
              <Link className="trs-noprint" href={metricHref(slug, s.query)}>Re-run the query</Link>
            </>
          )}
          {s.note && <> {s.note}</>}
        </li>
      ))}
    </ol>
  );
}
