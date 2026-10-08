"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Ask Clefs console (02 spec 7, Canon "Ask"). Composer on top, answers
   newest first beneath it, saved views, recent questions and the WhatsApp
   opt-in in the sidebar.

   - Every question is one POST /ask; the model only picks registry keys
     and the server checks every number, so the card renders what comes
     back as-is (no client-side maths on figures).
   - Saved views and recent questions never call the model: a view re-runs
     its stored query (POST /views/{id}/run), a recent question reopens its
     stored answer (the log row).
   - ?log={id} is the link the WhatsApp reply carries (via a magic link);
     ?view={id} runs a saved view straight away. Both come from page.tsx.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LuMessageSquare } from "react-icons/lu";
import { api, useApi, withQuery } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Card, EmptyState, PageHeader, useToast } from "../../../../ui";
import AnswerCard, { type AnswerEntry } from "./AnswerCard";
import AskComposer from "./AskComposer";
import { RecentCard, SavedViewsCard, type ViewOps } from "./SavedViews";
import WhatsAppCard from "./WhatsAppCard";
import { answerFromLog, askApi, askErrorText, catalogLabels, PERM, starterQuestions } from "./lib";
import { askStyles } from "./styles";
import type { Answer, CatalogMetric, LogRow, SavedView } from "./types";

/** Answers kept on screen; older ones stay a tap away under Recent. */
const MAX_ENTRIES = 20;

/** Server order for views: pinned first, then newest. */
function sortViews(list: SavedView[]): SavedView[] {
  return [...list].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.created_at.localeCompare(a.created_at) || b.id - a.id);
}

let tempSeq = 0;
const tempKey = (prefix: string) => `${prefix}-tmp-${++tempSeq}`;

export default function AskConsole({ initialLogId, initialViewId }: {
  initialLogId: number | null;
  initialViewId: number | null;
}) {
  const { slug } = useHotel();
  const { can, loading: sessionLoading } = useSession();
  const allowed = can(PERM.view);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: askStyles }} />
      <PageHeader
        eyebrow="Ask Clefs"
        title="Ask your figures"
        subtitle="Plain questions over your hotel's own metrics. Every number is checked against the data, and you can see exactly how each one was computed."
      />
      {!allowed && !sessionLoading ? (
        <Card>
          <EmptyState icon={<LuMessageSquare />} title="Ask Clefs is not part of your role"
                      body="Owners, GMs, finance, revenue and department heads can ask questions here. Ask your GM for access." />
        </Card>
      ) : allowed ? (
        <AskWorkspace key={slug} slug={slug} initialLogId={initialLogId} initialViewId={initialViewId} />
      ) : null}
    </>
  );
}

// ─── Workspace ───────────────────────────────────────────────────────────

function initialEntries(logId: number | null, viewId: number | null): AnswerEntry[] {
  if (logId !== null) return [{ key: `log-${logId}`, question: "Opening the answer…", answer: null, pending: true }];
  if (viewId !== null) return [{ key: `view-${viewId}-deeplink`, question: "Running the saved view…", answer: null, pending: true }];
  return [];
}

function entryFromLog(log: LogRow): AnswerEntry {
  return {
    key: `log-${log.id}`, question: log.question, answer: answerFromLog(log), channel: log.channel,
    feedback: log.feedback, feedbackNote: log.feedback_note,
  };
}

function AskWorkspace({ slug, initialLogId, initialViewId }: {
  slug: string;
  initialLogId: number | null;
  initialViewId: number | null;
}) {
  const toast = useToast();
  const catalogQ = useApi<CatalogMetric[]>(askApi(slug, "/metrics"), [slug]);
  const viewsQ = useApi<SavedView[]>(askApi(slug, "/views"), [slug]);
  const logsQ = useApi<LogRow[]>(withQuery(askApi(slug, "/logs"), { limit: 20 }), [slug]);
  const [entries, setEntries] = useState<AnswerEntry[]>(() => initialEntries(initialLogId, initialViewId));
  const [focusKey, setFocusKey] = useState<string | null>(() => initialEntries(initialLogId, initialViewId)[0]?.key ?? null);
  const [runningId, setRunningId] = useState<number | null>(null);
  const [activeViewId, setActiveViewId] = useState<number | null>(initialViewId);
  // Only a typed question in flight holds the Ask button; a view run or a
  // deep-linked answer loading does not.
  const asking = entries.some(e => e.pending && e.key.startsWith("q-"));

  const catalog = useMemo(() => new Map((catalogQ.data ?? []).map(m => [m.key, m])), [catalogQ.data]);
  const labels = useMemo(() => catalogLabels(catalogQ.data), [catalogQ.data]);
  const starters = useMemo(() => (entries.length ? [] : starterQuestions(catalogQ.data)), [entries.length, catalogQ.data]);
  const reloadLogs = logsQ.reload;

  /** Replace one entry by key (or prepend it), keeping the list bounded. */
  const put = useCallback((key: string, next: AnswerEntry) => {
    setEntries(prev => {
      const i = prev.findIndex(e => e.key === key);
      const rest = i >= 0 ? [...prev.slice(0, i), next, ...prev.slice(i + 1)] : [next, ...prev];
      // A finished answer that is already on screen under its log key: keep one.
      return rest.filter((e, j) => rest.findIndex(o => o.key === e.key) === j).slice(0, MAX_ENTRIES);
    });
  }, []);

  // ─── Ask ───

  const ask = useCallback(async (question: string) => {
    const key = tempKey("q");
    put(key, { key, question, answer: null, pending: true, channel: "WEB" });
    setFocusKey(key);
    try {
      const a = await api.post<Answer>(askApi(slug), { question });
      const done: AnswerEntry = { key: a.log_id != null ? `log-${a.log_id}` : key, question, answer: a, channel: "WEB" };
      setEntries(prev => prev.map(e => (e.key === key ? done : e)));
      setFocusKey(done.key);
      reloadLogs();
    } catch (err) {
      setEntries(prev => prev.map(e => (e.key === key ? { ...e, pending: false, error: askErrorText(err) } : e)));
    }
  }, [slug, put, reloadLogs]);


  // ─── Saved views ───

  const setViews = viewsQ.setData;

  const runView = useCallback(async (view: Pick<SavedView, "id" | "title">, key = tempKey(`view-${view.id}`)) => {
    put(key, { key, question: view.title, answer: null, pending: true, channel: "VIEW" });
    setFocusKey(key);
    setRunningId(view.id);
    setActiveViewId(view.id);
    try {
      const a = await api.post<Answer>(askApi(slug, `/views/${view.id}/run`));
      setEntries(prev => prev.map(e => (e.key === key ? { key, question: a.view?.title ?? view.title, answer: a, channel: "VIEW" } : e)));
      if (a.view) {
        const fresh = a.view;
        setViews(prev => sortViews((prev ?? []).map(v => (v.id === fresh.id ? fresh : v))));
      }
    } catch (err) {
      setEntries(prev => prev.map(e => (e.key === key ? { ...e, pending: false, error: askErrorText(err) } : e)));
    } finally {
      setRunningId(r => (r === view.id ? null : r));
    }
  }, [slug, put, setViews]);

  const onViewSaved = useCallback((view: SavedView) => {
    setViews(prev => sortViews([view, ...(prev ?? []).filter(v => v.id !== view.id)]));
  }, [setViews]);

  const ops: ViewOps = useMemo(() => ({
    run: v => void runView(v),
    rename: async (v, title) => {
      try {
        const next = await api.patch<SavedView>(askApi(slug, `/views/${v.id}`), { title });
        setViews(prev => sortViews((prev ?? []).map(x => (x.id === next.id ? next : x))));
        return true;
      } catch (err) {
        toast.error("Could not rename the view", askErrorText(err));
        return false;
      }
    },
    pin: async (v, pinned) => {
      try {
        const next = await api.patch<SavedView>(askApi(slug, `/views/${v.id}`), { pinned });
        setViews(prev => sortViews((prev ?? []).map(x => (x.id === next.id ? next : x))));
        return true;
      } catch (err) {
        toast.error(pinned ? "Could not pin the view" : "Could not unpin the view", askErrorText(err));
        return false;
      }
    },
    remove: async v => {
      try {
        await api.del(askApi(slug, `/views/${v.id}`));
        setViews(prev => (prev ?? []).filter(x => x.id !== v.id));
        toast.info("View deleted");
        return true;
      } catch (err) {
        toast.error("Could not delete the view", askErrorText(err));
        return false;
      }
    },
  }), [slug, runView, setViews, toast]);

  /** Drop the failed card and send it again: a question re-asks, a saved
      view re-runs (no model). A failed deep-link log has nothing to retry. */
  const retry = useCallback((entry: AnswerEntry) => {
    setEntries(prev => prev.filter(e => e.key !== entry.key));
    const viewId = viewIdOf(entry.key);
    if (entry.channel === "VIEW" || Number.isFinite(viewId)) {
      if (Number.isFinite(viewId)) void runView({ id: viewId, title: entry.question });
    } else {
      void ask(entry.question);
    }
  }, [ask, runView]);

  // ─── Recent questions ───

  const openLog = useCallback((log: LogRow) => {
    const key = `log-${log.id}`;
    setEntries(prev => (prev.some(e => e.key === key) ? prev : [entryFromLog(log), ...prev].slice(0, MAX_ENTRIES)));
    setFocusKey(key);
  }, []);

  // ─── Deep links (?log= from WhatsApp, ?view=) ───
  // The pending card is already in the initial state, so the effect only
  // settles it when the fetch returns (no synchronous setState in here).

  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current) return;
    deepLinked.current = true;
    if (initialLogId !== null) {
      const key = `log-${initialLogId}`;
      api.get<LogRow>(askApi(slug, `/logs/${initialLogId}`))
        .then(log => setEntries(prev => prev.map(e => (e.key === key ? entryFromLog(log) : e))))
        .catch(err => setEntries(prev => prev.map(e => (e.key === key
          ? { ...e, question: "The answer from your link", pending: false, error: askErrorText(err) } : e))));
    } else if (initialViewId !== null) {
      const key = `view-${initialViewId}-deeplink`;
      api.post<Answer>(askApi(slug, `/views/${initialViewId}/run`))
        .then(a => {
          setEntries(prev => prev.map(e => (e.key === key
            ? { key, question: a.view?.title ?? "Saved view", answer: a, channel: "VIEW" } : e)));
          const fresh = a.view;
          if (fresh) setViews(prev => sortViews((prev ?? []).map(v => (v.id === fresh.id ? fresh : v))));
        })
        .catch(err => setEntries(prev => prev.map(e => (e.key === key
          ? { ...e, question: "Saved view", pending: false, error: askErrorText(err) } : e))));
    }
  }, [slug, initialLogId, initialViewId, setViews]);

  // Bring the newest or reopened answer into view (DOM only, no state).
  useEffect(() => {
    if (!focusKey || typeof document === "undefined") return;
    const el = document.getElementById(`ask-entry-${focusKey}`);
    el?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [focusKey]);

  // ─── Render ───

  const noMetrics = !!catalogQ.data && catalogQ.data.length === 0;

  return (
    <div className="ask-layout">
      <div className="ask-main">
        <AskComposer
          onAsk={q => void ask(q)}
          busy={asking}
          starters={starters}
          disabled={noMetrics}
          disabledNote={noMetrics ? "Your role has no metrics to ask about here yet." : null}
        />
        {entries.length === 0 ? (
          <Card>
            <EmptyState
              icon={<LuMessageSquare />}
              title="Ask anything about your own figures"
              body="Revenue, occupancy, ADR, requests, response times, reviews: by day, week or month, by channel or department, compared with last period or last year. Guest-level details are never shown."
            />
          </Card>
        ) : (
          <ol className="ask-thread" aria-label="Answers, newest first">
            {entries.map(e => (
              <li key={e.key} id={`ask-entry-${e.key}`}>
                <AnswerCard
                  entry={e}
                  catalog={catalog}
                  labels={labels}
                  focus={initialLogId !== null && e.key === `log-${initialLogId}`}
                  onAsk={q => void ask(q)}
                  onViewSaved={onViewSaved}
                  onRetry={e.error && !e.key.startsWith("log-") ? () => retry(e) : undefined}
                />
              </li>
            ))}
          </ol>
        )}
      </div>
      <aside className="ask-side" aria-label="Saved views and history">
        <SavedViewsCard
          views={viewsQ.data}
          loading={viewsQ.loading}
          error={viewsQ.error ? askErrorText(viewsQ.error) : null}
          runningId={runningId}
          activeId={activeViewId}
          ops={ops}
        />
        <RecentCard logs={logsQ.data} loading={logsQ.loading} activeKey={focusKey} onOpen={openLog} />
        <WhatsAppCard />
      </aside>
    </div>
  );
}

/** "view-12-tmp-3" / "view-12-deeplink" -> 12. */
function viewIdOf(key: string): number {
  const m = /^view-(\d+)/.exec(key);
  return m ? Number(m[1]) : NaN;
}
