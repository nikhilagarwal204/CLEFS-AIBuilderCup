"use client";

/* AI answers tab (04 spec 5.5, 5.11): how often answer engines name the
   hotel. Every rate is a sample: it shows with its Wilson CI and n, or as
   "insufficient sample (n=...)" below the gate, and every engine carries
   its label ("API simulation: consumer app answers vary" or "Google AI Mode
   via SerpApi (third-party); indicative"). Fact errors open the evidence
   drawer; "Create fix" proposes the routed action. */

import { useState } from "react";
import { apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, Donut, SERIES_COLORS, Segmented, useToast } from "../../../../ui";
import type { AiCell, AiFact, AiRun, AiView } from "./types";
import { ERROR_TYPE_TEXT, cellView, engineLabel, localeLabel, mixSlices, sovSegments } from "./model";
import { CiBar, SetupChecklist, SourceLabel, TabState, errText } from "./parts";
import EvidenceDrawer, { type EvidenceTarget } from "./EvidenceDrawer";

// ─── Cells ───────────────────────────────────────────────────────────────

function Cell({ c }: { c: AiCell }) {
  const v = cellView(c);
  return (
    <div className="vis-cell">
      <div className="vis-cell__head">{localeLabel(c.market, c.language)} · {c.window_weeks === 1 ? "this week" : `last ${c.window_weeks} weeks`}</div>
      <div className={v.sufficient ? "vis-cell__value" : "vis-cell__value vis-cell__value--thin"}>{v.text}</div>
      {v.sufficient && v.rate !== null && (
        <CiBar rate={v.rate} low={v.low} high={v.high} label={`Mentioned in ${v.text} of answers, n=${v.n}`} />
      )}
      <div className="vis-muted">n={v.n} answers · {c.mentions} mention you</div>
    </div>
  );
}

function CellsCard({ view }: { view: AiView }) {
  const engines = Array.from(new Set(view.cells.map(c => c.engine)));
  return (
    <Card title="Mention rate" subtitle={`Share of answers that name you, with a 95% interval. Shown once a cell has ${view.min_sample.runs}+ answers and a tight enough interval.`}>
      {engines.length === 0 && <p className="vis-muted">No weekly results yet. The radar runs every Monday at 01:00.</p>}
      <div className="vis-stack">
        {engines.map(e => {
          const cells = view.cells.filter(c => c.engine === e);
          return (
            <div key={e} className="vis-stack" style={{ gap: 8 }}>
              <div className="vis-row"><strong>{engineLabel(e)}</strong><span className="vis-spacer" /><SourceLabel>{cells[0]?.label}</SourceLabel></div>
              <div className="vis-cells">{cells.map(c => <Cell key={`${c.market}-${c.language}`} c={c} />)}</div>
            </div>
          );
        })}
      </div>
      <p className="vis-note">{view.method_note}</p>
    </Card>
  );
}

// ─── Share of voice and source mix ───────────────────────────────────────

function SovCard({ view }: { view: AiView }) {
  return (
    <Card title="Share of voice" subtitle="Of all hotel mentions in these answers, how many are you versus your comp set.">
      {view.sov.length === 0 && <p className="vis-muted">No answers yet.</p>}
      <div className="vis-stack">
        {view.sov.map(s => {
          const segs = sovSegments(s.entities, s.sufficient);
          const n = view.cells.find(c => c.engine === s.engine && c.market === s.market && c.language === s.language)?.runs ?? 0;
          return (
            <div key={`${s.engine}-${s.market}-${s.language}`}>
              <div className="vis-row" style={{ marginBottom: 6 }}>
                <strong>{engineLabel(s.engine)}</strong><span className="vis-muted">{localeLabel(s.market, s.language)}</span>
              </div>
              {segs.length === 0 ? <p className="vis-muted">insufficient sample (n={n})</p> : (
                <>
                  <div className="vis-sov" role="img" aria-label={segs.map(x => `${x.name} ${x.text}`).join(", ")}>
                    {segs.map((x, i) => (
                      <span key={x.key} className="vis-sov__seg" title={`${x.name}: ${x.text}`}
                        style={{ width: `${x.pct}%`, background: x.key === "other" ? "var(--clefs-line)" : SERIES_COLORS[i % SERIES_COLORS.length] }} />
                    ))}
                  </div>
                  <ul className="vis-legend">
                    {segs.map((x, i) => (
                      <li key={x.key}><i style={{ background: x.key === "other" ? "var(--clefs-line)" : SERIES_COLORS[i % SERIES_COLORS.length] }} />
                        {x.self ? <strong>{x.name}</strong> : x.name} {x.text}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

type MixSide = "all" | "self_wins" | "comp_wins";

function SourceMixCard({ view }: { view: AiView }) {
  const engines = Object.keys(view.source_mix);
  const [engine, setEngine] = useState<string>(engines[0] ?? "");
  const [side, setSide] = useState<MixSide>("all");
  const e = engines.includes(engine) ? engine : engines[0];
  const slices = mixSlices(e ? view.source_mix[e]?.[side] : null);
  return (
    <Card title="Where answers get their facts" subtitle="Cited domains by type, home market in English.">
      {engines.length > 1 && (
        <Segmented size="sm" ariaLabel="Engine" value={e} onChange={setEngine}
          options={engines.map(k => ({ value: k, label: engineLabel(k) }))} />
      )}
      <div style={{ margin: "10px 0" }}>
        <Segmented size="sm" ariaLabel="Answers" value={side} onChange={setSide}
          options={[{ value: "all", label: "All" }, { value: "self_wins", label: "Naming you" }, { value: "comp_wins", label: "Naming rivals" }]} />
      </div>
      <Donut slices={slices} maxSlices={5} ariaLabel="Cited source types" emptyNote="No citations yet." />
    </Card>
  );
}

// ─── Fact errors and recent answers ──────────────────────────────────────

function FactsCard({ facts, onOpen, onDismiss, busy }: {
  facts: AiFact[]; onOpen: (f: AiFact) => void; onDismiss: (f: AiFact) => void; busy: number | null;
}) {
  const fmt = useFormat();
  const { can } = useSession();
  return (
    <Card title="Wrong facts in AI answers" subtitle="Checked against your knowledge base. Open one to see the answer and its sources.">
      {facts.length === 0 ? <p className="vis-muted">No open fact errors.</p> : (
        <ul className="vis-facts">
          {facts.map(f => (
            <li key={f.id} className="vis-fact">
              <div className="vis-fact__head">
                <span className="vis-fact__title">{f.label}</span>
                <Badge tone={f.severity <= 1 ? "bad" : "warn"}>{ERROR_TYPE_TEXT[f.error_type] ?? f.error_type}</Badge>
                {f.status === "recurred" && <Badge tone="warn">Seen again</Badge>}
                {f.status === "fixing" && <Badge tone="info">Fix proposed</Badge>}
                <span className="vis-spacer" />
                <span className="vis-muted">{f.occurrences}x · {fmt.relative(f.last_seen)}</span>
              </div>
              {f.claimed_quote && <p className="vis-quote" dir="auto">&ldquo;{f.claimed_quote}&rdquo;</p>}
              <div className="vis-muted">
                Answer: {f.claimed_value ?? "n/a"} · Yours: {f.truth_value ?? "not recorded"} · {f.engines.map(engineLabel).join(", ")}
                {f.cited_domains.length > 0 && <> · cites {f.cited_domains.slice(0, 3).join(", ")}</>}
              </div>
              <div className="vis-row">
                <Button size="sm" onClick={() => onOpen(f)} disabled={!f.run_ids.length}>View evidence</Button>
                {can("visibility.act") && f.status !== "fixing" && (
                  <Button size="sm" variant="ghost" onClick={() => onDismiss(f)} loading={busy === f.id}>Not an error</Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RecentAnswers({ onOpen }: { onOpen: (runId: number) => void }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const r = useApi<{ runs: AiRun[] }>(hotelPath(slug, "/visibility/ai/runs?limit=15"), [slug]);
  const runs = r.data?.runs ?? [];
  return (
    <Card title="Recent answers" subtitle="The raw answers behind the numbers.">
      {r.error && <p className="vis-muted">{errText(r.error)}</p>}
      {!r.error && runs.length === 0 && <p className="vis-muted">{r.loading ? "Loading..." : "No answers yet."}</p>}
      <ul className="vis-cites">
        {runs.map(run => (
          <li key={run.id}>
            <button type="button" className="vis-link" onClick={() => onOpen(run.id)} dir="auto">{run.prompt}</button>
            <span className="vis-muted"> {engineLabel(run.engine)} · {localeLabel(run.market, run.language)} · {run.run_at ? fmt.relative(run.run_at) : ""}
              {run.self_mentioned ? " · mentions you" : ""}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function EpochNotes({ view, onDecide, busy }: { view: AiView; onDecide: (engine: string, key: string, accept: boolean) => void; busy: string | null }) {
  const { can } = useSession();
  const notes = view.epochs.filter(e => e.status === "active" && e.note);
  const suggested = view.epochs.filter(e => e.status === "suggested");
  if (!notes.length && !suggested.length) return null;
  return (
    <Card title="Model changes" subtitle="When an engine's model or sources change, the baseline resets so weeks stay comparable.">
      <ul className="vis-cites">
        {notes.map(e => <li key={`${e.engine}-${e.epoch}`}>{engineLabel(e.engine)}: {e.note}</li>)}
        {suggested.map(e => (
          <li key={e.version_key} className="vis-row">
            <span>{engineLabel(e.engine)}: sources shifted{e.reason ? ` (${e.reason})` : ""}. Reset the baseline?</span>
            {can("visibility.manage") && (
              <>
                <Button size="sm" onClick={() => onDecide(e.engine, e.version_key, true)} loading={busy === e.version_key}>Reset</Button>
                <Button size="sm" variant="ghost" onClick={() => onDecide(e.engine, e.version_key, false)}>Keep</Button>
              </>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ─── Tab ─────────────────────────────────────────────────────────────────

export default function AiTab({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { slug } = useHotel();
  const toast = useToast();
  const a = useApi<AiView>(hotelPath(slug, "/visibility/ai"), [slug]);
  const [target, setTarget] = useState<EvidenceTarget | null>(null);
  const [fixing, setFixing] = useState(false);
  const [busyFact, setBusyFact] = useState<number | null>(null);
  const [busyReset, setBusyReset] = useState<string | null>(null);

  const fix = async (f: AiFact) => {
    setFixing(true);
    try {
      await apiFetch(hotelPath(slug, `/visibility/ai/facts/${f.id}/fix`), { method: "POST" });
      toast.success("Fix proposed", "It waits for approval in your action list, unless the autonomy dial runs it.");
      setTarget(t => (t?.fact?.id === f.id ? { ...t, fact: { ...f, status: "fixing" } } : t));
      a.reload();
    } catch (err) {
      toast.error("Could not create the fix", errText(err));
    } finally {
      setFixing(false);
    }
  };
  const dismiss = async (f: AiFact) => {
    setBusyFact(f.id);
    try {
      await apiFetch(hotelPath(slug, `/visibility/ai/facts/${f.id}/dismiss`), { method: "POST" });
      a.setData(prev => (prev ? { ...prev, facts: prev.facts.filter(x => x.id !== f.id) } : (prev as unknown as AiView)));
    } catch (err) {
      toast.error("Could not dismiss", errText(err));
    } finally {
      setBusyFact(null);
    }
  };
  const decide = async (engine: string, version_key: string, accept: boolean) => {
    setBusyReset(version_key);
    try {
      await apiFetch(hotelPath(slug, `/visibility/ai/engines/${encodeURIComponent(engine)}/baseline`), { method: "POST", json: { version_key, accept } });
      a.reload();
    } catch (err) {
      toast.error("Could not save", errText(err));
    } finally {
      setBusyReset(null);
    }
  };

  return (
    <TabState loading={a.loading && !a.data} error={a.error} onRetry={a.reload}>
      {a.data && (
        <div className="vis-stack">
          {!a.data.enabled && (
            <SetupChecklist title="Set up the AI radar" codes={a.data.setup_missing} onOpenSettings={onOpenSettings}
              intro="Answer engines are sampled weekly once at least one is available. Missing keys are not errors." />
          )}
          <Card padded>
            <div className="vis-stack" style={{ gap: 6 }}>
              <SourceLabel>{a.data.labels.simulation}</SourceLabel>
              <SourceLabel>{a.data.labels.third_party}</SourceLabel>
              <div className="vis-row">
                {a.data.engines.map(e => (
                  <Badge key={e.engine} tone={e.status === "ok" ? "ok" : "neutral"} dot title={e.disclaimer}>
                    {e.label}: {e.status === "ok" ? "on" : e.status.replace(/_/g, " ")}
                  </Badge>
                ))}
                {a.data.week && <span className="vis-muted">Week of {a.data.week}</span>}
              </div>
            </div>
          </Card>
          <EpochNotes view={a.data} onDecide={(e, k, ok) => void decide(e, k, ok)} busy={busyReset} />
          <CellsCard view={a.data} />
          <div className="vis-two">
            <SovCard view={a.data} />
            <SourceMixCard view={a.data} />
          </div>
          <FactsCard facts={a.data.facts} busy={busyFact} onDismiss={f => void dismiss(f)}
            onOpen={f => setTarget({ runIds: f.run_ids, fact: f })} />
          <RecentAnswers onOpen={id => setTarget({ runIds: [id] })} />
        </div>
      )}
      <EvidenceDrawer target={target} onClose={() => setTarget(null)} onFix={f => void fix(f)} fixing={fixing} />
    </TabState>
  );
}
