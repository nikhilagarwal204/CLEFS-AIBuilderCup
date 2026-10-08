"use client";

/* ═══════════════════════════════════════════════════════════════════════
   AnswerCard (02 spec 7): one question and its answer. Reads top-down the
   way an owner checks a number: the sentence, the figure or chart, the
   table it came from, then "How this was computed". Up to three result
   blocks (one per query the model ran). Declined and unsupported answers
   say why in plain words and offer the model's suggested rephrasings as
   one-tap questions.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuBookmarkPlus, LuCheck, LuMessageCircle } from "react-icons/lu";
import { api } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { Badge, Button, Card, Skeleton, cx, useToast } from "../../../../ui";
import AnswerTable from "./AnswerTable";
import ChartSpecRenderer from "./ChartSpecRenderer";
import Feedback from "./Feedback";
import TraceDisclosure from "./TraceDisclosure";
import { askApi, askErrorText, compareText, reasonText, resultsOf, statusMeta, viewTitle } from "./lib";
import type { Answer, CatalogMetric, ResultBlock, SavedView } from "./types";

export interface AnswerEntry {
  /** Stable client key (log id, or a temporary one while pending). */
  key: string;
  question: string;
  answer: Answer | null;
  /** Set while the question is in flight or when it failed outright. */
  pending?: boolean;
  error?: string | null;
  channel?: "WEB" | "WHATSAPP" | "VIEW";
  feedback?: 1 | -1 | null;
  feedbackNote?: string | null;
}

export default function AnswerCard({ entry, catalog, labels, focus = false, onAsk, onViewSaved, onRetry }: {
  entry: AnswerEntry;
  catalog: Map<string, CatalogMetric>;
  labels: Record<string, string>;
  focus?: boolean;
  onAsk: (question: string) => void;
  onViewSaved: (view: SavedView) => void;
  onRetry?: () => void;
}) {
  const a = entry.answer;
  if (entry.pending || !a) {
    return (
      <Card as="article" className={cx("ask-card", focus && "ask-card--focus")}>
        <div className="ask-card__q"><p className="ask-card__question">{entry.question}</p></div>
        {entry.pending ? (
          <div aria-live="polite" aria-busy="true">
            <span className="cx-sr">Working out the answer</span>
            <Skeleton lines={2} height={18} />
            <div style={{ height: 12 }} />
            <Skeleton height={120} radius={12} />
          </div>
        ) : (
          <div role="alert" className="ask-card__note" style={{ display: "grid", gap: 10 }}>
            <span>{entry.error || "Something went wrong."}</span>
            {onRetry && <span><Button size="sm" onClick={onRetry}>Try again</Button></span>}
          </div>
        )}
      </Card>
    );
  }

  const results = resultsOf(a);
  const status = statusMeta(a.status);
  // The server writes its own sentence for every decline and error; the
  // console's wording is only a fallback for a row stored without one.
  const reason = a.status !== "answered" && !a.answer_text?.trim() ? reasonText(a.reason) : null;
  const viewRun = entry.channel === "VIEW" || !!a.view;
  const multi = results.length > 1;

  return (
    <Card as="article" className={cx("ask-card", focus && "ask-card--focus")}>
      <div className="ask-card__q">
        <p className="ask-card__question">{a.question || entry.question}</p>
        <div className="ask-card__tags">
          {viewRun && <Badge tone="gold">Saved view</Badge>}
          {entry.channel === "WHATSAPP" && <Badge tone="info"><LuMessageCircle aria-hidden="true" /> WhatsApp</Badge>}
          {a.status !== "answered" && <Badge tone={status.tone} dot>{status.label}</Badge>}
        </div>
      </div>

      {a.answer_text && <p className="ask-card__text">{a.answer_text}</p>}
      {reason && <p className="ask-card__note">{reason}</p>}

      {a.suggestions?.length > 0 && (
        <div className="ask-chips" aria-label="Try asking instead">
          {a.suggestions.slice(0, 4).map(s => (
            <button key={s} type="button" className="ask-chip" onClick={() => onAsk(s)}>{s}</button>
          ))}
        </div>
      )}

      {results.map((r, i) => (
        <ResultView key={i} index={i} result={r} answer={a} multi={multi} catalog={catalog} labels={labels}
                    viewRun={viewRun} onViewSaved={onViewSaved} question={a.question || entry.question} />
      ))}

      <div className="ask-card__foot">
        <div className="ask-card__actions">
          {a.log_id !== null && a.log_id !== undefined && !viewRun && (
            <Feedback logId={a.log_id} initial={entry.feedback ?? null} initialNote={entry.feedbackNote} />
          )}
          {!multi && !viewRun && results[0]?.query && a.log_id != null && (
            <SaveViewButton logId={a.log_id} index={0} question={a.question || entry.question} onSaved={onViewSaved} />
          )}
        </div>
        <span className="ask-card__meta">
          {typeof a.latency_ms === "number" && a.latency_ms >= 100 ? `Answered in ${(a.latency_ms / 1000).toFixed(1)} s` : ""}
        </span>
      </div>
    </Card>
  );
}

// ─── One result block ────────────────────────────────────────────────────

function ResultView({ index, result, answer, multi, catalog, labels, viewRun, onViewSaved, question }: {
  index: number;
  result: ResultBlock;
  answer: Answer;
  multi: boolean;
  catalog: Map<string, CatalogMetric>;
  labels: Record<string, string>;
  viewRun: boolean;
  onViewSaved: (view: SavedView) => void;
  question: string;
}) {
  const t = result.trace;
  const title = t ? `${t.metric_label}, ${t.period.label.toLowerCase()}` : `Result ${index + 1}`;
  const entry = t ? catalog.get(t.metric) : undefined;
  const isKpi = result.chart_spec?.type === "kpi";
  // A lone KPI's table is one row repeating the big figure: keep it for the
  // comparison columns only, which the KPI chip already shows in part.
  const showTable = !!result.table && !(isKpi && !result.table.columns.some(c => c.kind === "compare"));

  return (
    <section className="ask-result" aria-label={title}>
      {multi && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
          <h3 className="ask-result__title">{title}</h3>
          {!viewRun && result.query && answer.log_id != null && (
            <SaveViewButton logId={answer.log_id} index={index} question={`${question} (${t?.metric_label ?? index + 1})`}
                            onSaved={onViewSaved} />
          )}
        </div>
      )}
      <ChartSpecRenderer spec={result.chart_spec} grain={t?.grain} title={title}
                         higherIsBetter={entry?.higher_is_better ?? null} compareLabel={compareText(t?.compare)} />
      {showTable && result.table && <AnswerTable table={result.table} grain={t?.grain} title={title} />}
      {t && (
        <TraceDisclosure trace={t} labels={labels} answer={answer} first={index === 0} viewRun={viewRun} />
      )}
    </section>
  );
}

// ─── Save as a view ──────────────────────────────────────────────────────
/* Saves the validated query_metric arguments of that result (never SQL),
   titled from the question; rename or pin it in the sidebar. The server
   re-validates the query now and on every run. */

function SaveViewButton({ logId, index, question, onSaved }: {
  logId: number;
  index: number;
  question: string;
  onSaved: (view: SavedView) => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const view = await api.post<SavedView>(askApi(slug, "/views"), {
        title: viewTitle(question), log_id: logId, result_index: index,
      });
      setSaved(true);
      onSaved(view);
      toast.success("Saved to your views", "Run it any time without asking again.");
    } catch (err) {
      toast.error("Could not save the view", askErrorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button size="sm" variant="ghost" loading={busy} disabled={saved}
            icon={saved ? <LuCheck /> : <LuBookmarkPlus />} onClick={save}>
      {saved ? "Saved" : "Save view"}
    </Button>
  );
}
