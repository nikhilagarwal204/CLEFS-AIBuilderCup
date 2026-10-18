"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One evaluation run: pass rate per category against the gate thresholds,
   then every scenario result, failures first. A result opens to show the
   conversation, the tool calls the dry-run executor saw and what each
   grader decided, so a GM can tell a KB gap from a model mistake.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Drawer, EmptyState, Segmented, Skeleton } from "../../../../../ui";
import { catLabel, ratePct, trustApi } from "../lib";
import type { EvalResult, EvalRun } from "../types";

const SAFETY = new Set(["emergency", "injection", "refund_legal"]);
const ROUTING = new Set(["routing", "multilingual"]);

/** The gate threshold a category is held to (safety 100%, routing 95%, else 90%). */
function threshold(cat: string): number {
  return SAFETY.has(cat) ? 1 : ROUTING.has(cat) ? 0.95 : 0.9;
}

export function CategoryBars({ rates }: { rates: EvalRun["category_rates"] }) {
  const entries = Object.entries(rates ?? {}).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return <p className="cx-muted trs-small">No category results yet.</p>;
  return (
    <div className="trs-bars">
      {entries.map(([cat, r]) => {
        const rate = r.rate === null ? 0 : Number(r.rate);
        const need = threshold(cat);
        const cls = rate >= need ? "" : rate >= need - 0.1 ? " trs-bar__fill--warn" : " trs-bar__fill--bad";
        return (
          <div key={cat} className="trs-bar">
            <span>{catLabel(cat)}{SAFETY.has(cat) && <span className="cx-faint"> · safety</span>}</span>
            <span className="trs-bar__track" role="img" aria-label={`${ratePct(r.rate)} of ${r.total}, needs ${need * 100}%`}>
              <span className={`trs-bar__fill${cls}`} style={{ width: `${Math.max(2, rate * 100)}%`, display: "block" }} />
            </span>
            <span className="cx-num">{r.passed}/{r.total}</span>
          </div>
        );
      })}
    </div>
  );
}

function ResultItem({ r }: { r: EvalResult }) {
  const [open, setOpen] = useState(!r.passed);
  const msgs = r.transcript?.messages ?? [];
  return (
    <div className="trs-diff__row">
      <button type="button" className="trs-diff__path" onClick={() => setOpen(o => !o)} aria-expanded={open}
              style={{ background: "none", border: 0, color: "inherit", padding: 0, cursor: "pointer", width: "100%", textAlign: "left" }}>
        <Badge tone={r.passed ? "ok" : "bad"} dot>{r.passed ? "Pass" : "Fail"}</Badge>
        <span>{r.title || r.scenario_key}</span>
        <span className="cx-faint trs-small">{catLabel(r.category)}{r.language ? ` · ${r.language}` : ""}{r.attempts > 1 ? ` · ${r.attempts} tries` : ""}</span>
      </button>
      {open && (
        <div className="cx-stack" style={{ gap: 10, marginTop: 10 }}>
          {r.failure_reasons.length > 0 && (
            <ul className="trs-caveats">{r.failure_reasons.map((f, i) => <li key={i}>{f}</li>)}</ul>
          )}
          {msgs.length > 0 && (
            <div className="trs-chat">
              {msgs.map((m, i) => (
                <div key={i} className={`trs-msg trs-msg--${m.role === "user" ? "user" : "assistant"}`}>
                  <span className="trs-msg__role">{m.role === "user" ? "Guest" : "Concierge"}</span>
                  {m.content}
                </div>
              ))}
            </div>
          )}
          {r.transcript?.error && <p className="cx-muted trs-small">Runner error: {r.transcript.error}</p>}
          {r.tool_calls.length > 0 && (
            <div>
              <div className="trs-section-title">Tool calls</div>
              <pre className="trs-json trs-mono">{JSON.stringify(r.tool_calls, null, 2)}</pre>
            </div>
          )}
          {r.grader_outputs.length > 0 && (
            <div>
              <div className="trs-section-title">Graders</div>
              <ul className="trs-checks">
                {r.grader_outputs.map((g, i) => (
                  <li key={i} className="trs-check">
                    <span className={`trs-check__mark trs-check__mark--${g.skipped || g.passed ? "ok" : "bad"}`} aria-hidden="true">
                      {g.skipped ? "-" : g.passed ? "✓" : "✕"}
                    </span>
                    <span className="trs-small">
                      <span className="trs-mono">{g.grader}</span>{g.skipped ? " (skipped)" : ""}
                      {g.detail && <span className="cx-muted"> {g.detail}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function RunDrawer({ slug, runId, onClose }: { slug: string; runId: number | null; onClose: () => void }) {
  const fmt = useFormat();
  const [failedOnly, setFailedOnly] = useState(true);
  const run = useApi<EvalRun>(runId === null ? null : trustApi(slug, `/evals/runs/${runId}`), [slug, runId]);
  const results = useApi<EvalResult[]>(
    runId === null ? null : trustApi(slug, `/evals/runs/${runId}/results?failed_only=${failedOnly}`), [slug, runId, failedOnly]);
  const r = run.data && run.data.id === runId ? run.data : undefined;

  return (
    <Drawer open={runId !== null} onClose={onClose} wide title={r ? `Run #${r.id}` : "Evaluation run"}>
      {run.loading && !r && <Skeleton lines={6} />}
      {run.error && <EmptyState title="Couldn't load this run" body={run.error.message} />}
      {r && (
        <div className="cx-stack">
          <dl className="trs-kv">
            <dt>Result</dt>
            <dd>
              <strong>{ratePct(r.pass_rate)}</strong> ({r.passed ?? 0} of {r.total ?? 0})
              {r.previous_pass_rate != null && <span className="cx-muted"> · previous run {ratePct(r.previous_pass_rate)}</span>}
            </dd>
            <dt>Started</dt><dd>{r.started_at ? fmt.dateTime(r.started_at) : "Queued"}</dd>
            <dt>Trigger</dt><dd>{r.trigger.replace(/_/g, " ")}</dd>
            <dt>Prompt</dt><dd className="trs-mono">{r.prompt_version ?? "-"}</dd>
            <dt>Models</dt><dd className="trs-mono">{r.model ?? "-"} · judge {r.judge_model ?? "-"} · {r.runner ?? "-"}</dd>
            <dt>Cost</dt><dd>{r.cost_usd != null ? `USD ${Number(r.cost_usd).toFixed(4)}` : "-"}</dd>
            {r.error && (<><dt>Stopped</dt><dd>{r.error === "cost_cap_reached" ? "Daily cost cap reached" : r.error}</dd></>)}
          </dl>
          <div>
            <div className="trs-section-title">By category (gate: safety 100%, routing 95%, overall 90%)</div>
            <CategoryBars rates={r.category_rates} />
          </div>
          <div className="cx-row" style={{ justifyContent: "space-between" }}>
            <div className="trs-section-title" style={{ margin: 0 }}>Scenarios</div>
            <Segmented size="sm" ariaLabel="Which results" value={failedOnly ? "f" : "a"}
                       onChange={v => setFailedOnly(v === "f")}
                       options={[{ value: "f", label: "Failures" }, { value: "a", label: "All" }]} />
          </div>
          {results.loading && !results.data && <Skeleton lines={4} />}
          {results.data && results.data.length === 0 && (
            <p className="cx-muted trs-small">{failedOnly ? "Every scenario passed." : "No results recorded."}</p>
          )}
          <div className="trs-diff">{(results.data ?? []).map(x => <ResultItem key={x.id} r={x} />)}</div>
        </div>
      )}
    </Drawer>
  );
}
