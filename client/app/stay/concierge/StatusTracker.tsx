"use client";

/* ═══════════════════════════════════════════════════════════════════════
   StatusTracker: the guest's own requests and orders, live (03 spec 2.6).

   Each tool result or menu order that files work hands back a tracking
   token; the page keeps those per stay (storage.ts) and polls the public
   tracker GET /public/r/{token}: every 10 s while the page is visible, 60 s
   while hidden, and not at all once an item has settled (done and answered,
   or cancelled).

   A job (05 spec 3) shows as its "Job sent to staff" card (JobSentCard):
   who has it, by first name only, and how fast it was picked up; once done,
   "Is everything OK?", where "Not fixed" reopens it and tells a manager.
   Orders, and requests from before the cards, keep the compact row with
   "Resolved?". A surname never reaches this surface.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState } from "react";
import { LuListChecks } from "react-icons/lu";
import { ApiError } from "../../lib/api";
import { confirmTracker, fetchTracker, sendFeedback, type TrackerStatus } from "./api";
import { langDir, type TFn } from "./i18n";
import JobSentCard from "./JobSentCard";
import { isSettled, jobPhase, mergeJob, toMs } from "./jobs";
import type { TrackedItem } from "./storage";

const VISIBLE_MS = 10_000;
const HIDDEN_MS = 60_000;
const STEPS = ["received", "assigned", "on_the_way", "done"] as const;

// ─── Polling hook ────────────────────────────────────────────────────────

/** A patch for one tracked item: null drops it (a forged or expired token). */
export type TrackedPatch = Partial<TrackedItem> | null;

export function useTrackerStatuses(items: TrackedItem[], onPatch: (token: string, patch: TrackedPatch) => void) {
  const [statuses, setStatuses] = useState<Record<string, TrackerStatus>>({});
  const itemsRef = useRef(items);
  const patchRef = useRef(onPatch);
  // Mirrored in an effect (not during render) so the poll loop always sees
  // the latest list and callback without restarting on every render.
  useEffect(() => {
    itemsRef.current = items;
    patchRef.current = onPatch;
  }, [items, onPatch]);
  const tokensKey = items.map((i) => `${i.token}${i.done ? "+" : ""}`).join(",");

  useEffect(() => {
    if (!tokensKey) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let first = true;

    const tick = async () => {
      // The first pass reads every item (a reload must show settled ones
      // too); later passes skip what has settled.
      const due = itemsRef.current.filter((i) => first || !i.done);
      first = false;
      await Promise.all(due.map(async (i) => {
        try {
          const s = await fetchTracker(i.token);
          if (stopped) return;
          setStatuses((prev) => ({ ...prev, [i.token]: s }));
          const settled = isSettled(s);
          if (settled !== !!i.done) patchRef.current(i.token, { done: settled });
        } catch (err) {
          if (!stopped && err instanceof ApiError && err.status === 404) patchRef.current(i.token, null);
        }
      }));
      if (stopped) return;
      const live = itemsRef.current.some((i) => !i.done);
      if (live) timer = setTimeout(tick, document.hidden ? HIDDEN_MS : VISIBLE_MS);
    };

    const onVisible = () => {
      if (document.hidden || stopped) return;
      if (timer) clearTimeout(timer);
      void tick();
    };

    void tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [tokensKey]);

  return { statuses, setStatuses };
}

/** Date.now(), re-read every second while `running` (a card's live clock). */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  return now;
}

// ─── Pill ────────────────────────────────────────────────────────────────

/** Compact on purpose: it shares a phone-width header with four buttons. */
export function TrackerPill({ t, active, onClick, expanded, controls }: {
  t: TFn; active: number; onClick: () => void;
  /** Whether the tracker list is open (announced as aria-expanded). */
  expanded?: boolean;
  /** The list's element id, when it has one. */
  controls?: string;
}) {
  const label = active > 0 ? `${t("tracker.show")}: ${t("tracker.open", { n: active })}` : t("tracker.show");
  return (
    <button type="button" className="cc-pill" onClick={onClick} aria-label={label} title={label}
      aria-expanded={expanded} aria-controls={expanded ? controls : undefined}>
      <LuListChecks size={14} aria-hidden="true" />
      {active > 0 && (
        <>
          <span className="cc-pill-dot" aria-hidden="true" />
          <span>{active}</span>
        </>
      )}
    </button>
  );
}

// ─── List ────────────────────────────────────────────────────────────────

interface Props {
  t: TFn;
  lang: string;
  items: TrackedItem[];
  statuses: Record<string, TrackerStatus>;
  onStatus: (token: string, s: TrackerStatus) => void;
  onPatch: (token: string, patch: TrackedPatch) => void;
  /** wa.me number for the opt-in link; the link is hidden without one. */
  whatsappNumber?: string | null;
}

export default function StatusTracker({ t, lang, items, statuses, onStatus, onPatch, whatsappNumber }: Props) {
  const [answered, setAnswered] = useState<Record<string, "yes" | "no">>({});
  const [busy, setBusy] = useState<string | null>(null);
  // Newest first. A job card shows at once from its own snapshot; a plain
  // row waits for its first tracker read.
  const rows = [...items].reverse().filter((i) => i.card || statuses[i.token]);
  // One clock for every card still waiting to be accepted.
  const ticking = rows.some((i) => {
    if (!i.card) return false;
    const job = mergeJob(i.card, statuses[i.token]);
    return toMs(job.accepted_at) === null && !["done", "cancelled", "confirm"].includes(jobPhase(job, statuses[i.token], i.reopened));
  });
  const now = useNow(ticking);
  if (!rows.length) return null;

  const answer = async (token: string, resolved: boolean, job: boolean) => {
    setBusy(token);
    try {
      const s = job ? await sendFeedback(token, resolved) : await confirmTracker(token, resolved);
      // The answer's own response may be partial: read the job again for the card.
      const fresh = job ? await fetchTracker(token).catch(() => null) : (s as TrackerStatus);
      if (fresh) onStatus(token, fresh);
      setAnswered((a) => ({ ...a, [token]: resolved ? "yes" : "no" }));
      onPatch(token, resolved ? { done: true, answer: "yes", reopened: false } : { done: false, answer: "no", reopened: true });
    } catch {
      /* 409 cannot_confirm: the card simply stops offering the question */
    } finally {
      setBusy(null);
    }
  };

  const wa = (whatsappNumber || "").replace(/[^\d]/g, "");

  return (
    <div className="cc-tracker" dir={langDir(lang)} lang={lang} aria-live="polite">
      {rows.map((i) => {
        const s = statuses[i.token];
        if (i.card) {
          return (
            <JobSentCard
              key={i.token}
              t={t}
              lang={lang}
              job={mergeJob(i.card, s)}
              status={s}
              now={now}
              askedAt={i.askedAt}
              reopened={i.reopened}
              answer={answered[i.token] ?? i.answer ?? null}
              busy={busy === i.token}
              onAnswer={(fixed) => void answer(i.token, fixed, true)}
            />
          );
        }
        const label = (lang === "ar" && s.request_type_label_ar) || s.request_type_label
          || t(i.kind === "order" ? "tracker.order" : "tracker.request");
        const stateText = s.cancelled ? t("tracker.cancelled") : t(`tracker.${s.state}`);
        const reached = STEPS.indexOf(s.state);
        return (
          <div key={i.token} className="cc-track">
            <div className="cc-track-head">
              <span>{label}</span>
              <span className="cc-track-state">{stateText}</span>
            </div>
            {!s.cancelled && (
              <div className="cc-steps" aria-hidden="true">
                {STEPS.map((step, n) => (
                  <span key={step} className={n <= reached ? "cc-step cc-step-on" : "cc-step"} />
                ))}
              </div>
            )}
            {/* an answer belongs to one completion: once a reopened request is
                done again the server offers the question anew (can_confirm) */}
            {answered[i.token] && !s.can_confirm ? (
              <div className="cc-track-q">{t(answered[i.token] === "yes" ? "tracker.thanks" : "tracker.reopened")}</div>
            ) : s.can_confirm ? (
              <div className="cc-track-q">
                {t("tracker.resolvedQ")}
                <button type="button" className="cc-mini" disabled={busy === i.token} onClick={() => answer(i.token, true, false)}>
                  {t("tracker.yes")}
                </button>
                <button type="button" className="cc-mini" disabled={busy === i.token} onClick={() => answer(i.token, false, false)}>
                  {t("tracker.no")}
                </button>
              </div>
            ) : null}
            {wa && !s.cancelled && s.state !== "done" && (
              <div className="cc-track-q">
                <a
                  className="cc-link"
                  href={`https://wa.me/${wa}?text=${encodeURIComponent(`UPDATES ${i.token}`)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t("tracker.whatsapp")}
                </a>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
