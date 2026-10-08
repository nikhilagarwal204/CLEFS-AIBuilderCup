"use client";

/* ═══════════════════════════════════════════════════════════════════════
   "How this was computed" (02 spec 7): the governed query behind a figure,
   as plain labelled lines (pure.traceLines). An owner can check the metric,
   period, slicing and filters without trusting the prose, which is the
   point of Ask having no raw SQL: every figure comes from a named metric in
   the registry, read-only and scoped to this hotel.

   A closed <details> by default, so the answer stays one glance; the
   grounding outcome sits here too, since it is about how the text was
   checked, not about the figures.
   ═══════════════════════════════════════════════════════════════════════ */

import { traceLines } from "./lib";
import type { Answer, Trace } from "./types";

export default function TraceDisclosure({ trace, labels, answer, first, viewRun = false }: {
  trace: Trace;
  /** Catalog keys -> labels (metrics and dimensions). */
  labels: Record<string, string>;
  answer: Pick<Answer, "grounded" | "regenerated" | "status" | "tool_calls" | "ungrounded">;
  /** Only the first result's disclosure carries the whole-answer notes. */
  first: boolean;
  /** A saved view re-run: no model involved, the summary is deterministic. */
  viewRun?: boolean;
}) {
  const lines = traceLines(trace, labels);
  return (
    <details className="ask-trace">
      <summary>How this was computed</summary>
      <div className="ask-trace__body">
        {trace.description && <p className="ask-trace__desc">{trace.description}</p>}
        <dl className="ask-trace__list">
          {lines.map(l => (
            <div key={l.label} style={{ display: "contents" }}>
              <dt>{l.label}</dt>
              <dd className={l.mono ? "ask-trace__mono" : undefined}>{l.value}</dd>
            </div>
          ))}
        </dl>
        {first && <GroundingNote answer={answer} viewRun={viewRun} />}
      </div>
    </details>
  );
}

function GroundingNote({ answer, viewRun }: {
  answer: Pick<Answer, "grounded" | "regenerated" | "status" | "tool_calls" | "ungrounded">;
  viewRun: boolean;
}) {
  const notes: string[] = [];
  if (viewRun) {
    notes.push("Saved view: re-run straight from the metric, and the summary is written from the table with no AI involved.");
  } else if (answer.status === "table_only") {
    notes.push("The written answer used a number that is not in these results, so it was replaced by a plain summary of the table.");
    if (answer.ungrounded?.length) notes.push(`Numbers that failed the check: ${answer.ungrounded.join(", ")}.`);
  } else if (answer.grounded) {
    notes.push("Every number in the answer was checked against these results.");
    if (answer.regenerated) notes.push("The first draft failed that check and was rewritten once.");
  }
  if (!viewRun && typeof answer.tool_calls === "number" && answer.tool_calls > 0) {
    notes.push(`${answer.tool_calls} ${answer.tool_calls === 1 ? "lookup" : "lookups"} (at most three per question).`);
  }
  if (!notes.length) return null;
  return (
    <ul className="ask-trace__calls">
      {notes.map(n => <li key={n}>{n}</li>)}
    </ul>
  );
}
