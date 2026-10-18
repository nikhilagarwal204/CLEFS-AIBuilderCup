"use client";

/* Evidence drawer for one AI answer (04 spec 5.11): the raw answer with the
   claimed quote highlighted, every citation and search source as a
   clickable link (Google Search grounding terms require it), the model id, the
   run time, the source label and, for a fact error, "Create fix". A fact
   seen in several answers steps through its runs. */

import { useState } from "react";
import { hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Drawer, Skeleton } from "../../../../ui";
import type { AiFact, AiRun } from "./types";
import { ERROR_TYPE_TEXT, SOURCE_CLASS_LABELS, engineLabel, highlight, localeLabel, orderedCitations, safeHref } from "./model";
import { SourceLabel, errText } from "./parts";

export interface EvidenceTarget { runIds: number[]; fact?: AiFact | null }

export default function EvidenceDrawer({ target, onClose, onFix, fixing }: {
  target: EvidenceTarget | null;
  onClose: () => void;
  onFix: (fact: AiFact) => void;
  fixing: boolean;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const ids = target?.runIds ?? [];
  const [idx, setIdx] = useState(0);
  // Newest run first: run_ids are stored oldest to newest.
  const ordered = [...ids].reverse();
  const at = Math.min(idx, Math.max(0, ordered.length - 1));
  const runId = ordered[at];
  const r = useApi<AiRun>(target && runId !== undefined ? hotelPath(slug, `/visibility/ai/runs/${runId}`) : null, [slug, runId]);
  const fact = target?.fact ?? null;
  const run = r.data;

  const footer = fact && can("visibility.act") && fact.status !== "fixing" ? (
    <Button variant="primary" onClick={() => onFix(fact)} loading={fixing}>Create fix</Button>
  ) : fact?.status === "fixing" ? <Badge tone="info">Fix proposed</Badge> : undefined;

  return (
    <Drawer open={Boolean(target)} onClose={() => { setIdx(0); onClose(); }} wide footer={footer}
      title={fact ? `${fact.label}: ${ERROR_TYPE_TEXT[fact.error_type] ?? fact.error_type}` : "AI answer"}>
      {fact && (
        <dl className="vis-meta" style={{ marginBottom: 12 }}>
          <dt>Answer said</dt><dd>{fact.claimed_value ?? "n/a"}</dd>
          <dt>Your records</dt><dd>{fact.truth_value ?? "not in your knowledge base"}{fact.truth_source ? ` (${fact.truth_source})` : ""}</dd>
          <dt>Seen</dt><dd>{fact.occurrences} times, last {fmt.relative(fact.last_seen)}</dd>
        </dl>
      )}
      {ordered.length > 1 && (
        <div className="vis-row" style={{ marginBottom: 10 }}>
          <Button size="sm" variant="ghost" disabled={at === 0} onClick={() => setIdx(at - 1)}>Newer</Button>
          <span className="vis-muted">Answer {at + 1} of {ordered.length}</span>
          <Button size="sm" variant="ghost" disabled={at >= ordered.length - 1} onClick={() => setIdx(at + 1)}>Older</Button>
        </div>
      )}
      {r.loading && !run && <Skeleton lines={6} />}
      {r.error && <p className="vis-muted">{errText(r.error)}</p>}
      {run && <RunBody run={run} quote={fact?.claimed_quote ?? null} />}
    </Drawer>
  );
}

function RunBody({ run, quote }: { run: AiRun; quote: string | null }) {
  const fmt = useFormat();
  const cites = orderedCitations(run.citations);
  return (
    <div>
      <SourceLabel>{run.label}</SourceLabel>
      <dl className="vis-meta" style={{ marginTop: 10 }}>
        <dt>Engine</dt><dd>{engineLabel(run.engine)}</dd>
        <dt>Model</dt><dd>{run.model_id ?? "not reported"}</dd>
        <dt>Run at</dt><dd>{run.run_at ? fmt.dateTime(run.run_at) : "n/a"}</dd>
        <dt>Market</dt><dd>{localeLabel(run.market, run.language)}</dd>
        <dt>Prompt</dt><dd dir="auto">{run.prompt}</dd>
      </dl>
      <div className="vis-section">
        <h3>Answer</h3>
        <p className="vis-answer" dir="auto">
          {highlight(run.answer_text, quote).map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
          {!run.answer_text && <span className="vis-muted">The engine returned no text.</span>}
        </p>
        <p className="vis-note">
          {run.self_mentioned ? `Mentions you${run.self_position ? ` at position ${run.self_position}` : ""}.` : "Does not mention you."}
          {run.self_cited ? " Cites your website." : ""}
        </p>
      </div>
      <div className="vis-section">
        <h3>Citations and sources ({cites.length})</h3>
        {cites.length === 0 ? <p className="vis-muted">No sources were returned.</p> : (
          <ul className="vis-cites">
            {cites.map(c => (
              <li key={c.url}>
                <a href={safeHref(c.url) ?? undefined} target="_blank" rel="noopener noreferrer nofollow">{c.title || c.domain || c.url}</a>
                <span className="vis-muted"> {c.domain ?? ""}{c.class ? ` · ${SOURCE_CLASS_LABELS[c.class] ?? c.class}` : ""}{c.kind === "source" ? " · search source" : ""}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
