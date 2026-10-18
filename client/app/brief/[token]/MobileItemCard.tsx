"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One item of the mobile brief with its one-tap decisions. Approve and
   Remind are a single tap; Adjust, Dispute, Snooze and Ignore open a small
   inline step (an amount within the action's bounds, a reason, how long),
   never a new page. Assign needs the team list, which a link does not
   carry, so it opens the full brief in Clefs on that item instead.
   Every decision is re-checked server-side (link scope, role, state).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../lib/api";
import type { Formatter } from "../../lib/format";
import { ACTION_STATUS_LABELS, type Decision } from "../../lib/types";
import { decideError } from "../../app/[hotelSlug]/(console)/brief/lib";
import type { MobileAction, MobileDecideBody, MobileDecideResult, MobileItem } from "./types";

const LABEL: Record<Decision, string> = {
  approve: "Approve", adjust: "Adjust", assign: "Assign in Clefs", dispute: "Dispute",
  remind: "Remind", snooze: "Snooze", ignore: "Ignore",
};

const DONE: Record<Decision, string> = {
  approve: "Approved", adjust: "Adjusted and approved", assign: "Assigned", dispute: "Dispute submitted",
  remind: "Reminder sent", snooze: "Snoozed", ignore: "Ignored",
};

const IGNORE_REASONS: readonly [string, string][] = [
  ["wrong", "Not right"], ["done", "Already handled"], ["small", "Too small to act on"], ["other", "Other reason"],
];

type Step = "adjust" | "dispute" | "snooze" | "ignore" | null;

export default function MobileItemCard({ token, item, fmt, now, onUpdated, onOpenConsole }: {
  token: string;
  item: MobileItem;
  fmt: Formatter;
  /** Page-load time: "was due" vs "by" is judged once, not on every render. */
  now: number;
  onUpdated: (view: MobileAction) => void;
  onOpenConsole: (actionId: number | null) => void;
}) {
  const a = item.action;
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");

  const decide = async (body: MobileDecideBody) => {
    if (!a) return;
    setBusy(body.decision);
    setError(null);
    try {
      const out = await api.post<MobileDecideResult>(
        `/public/brief/${encodeURIComponent(token)}/actions/${a.id}/decide`, body, { silent401: true });
      onUpdated(out.view);
      setDone(DONE[body.decision]);
      setStep(null);
      setNote("");
      setAmount("");
    } catch (err) {
      setError(decideError(err));
    } finally {
      setBusy(null);
    }
  };

  const tap = (d: Decision) => {
    setError(null);
    setDone(null);
    if (d === "assign") return onOpenConsole(a?.id ?? null);
    if (d === "adjust" || d === "dispute" || d === "snooze" || d === "ignore") return setStep(s => (s === d ? null : d));
    void decide({ decision: d });
  };

  const overdue = item.kind === "overdue";
  const status = a ? ACTION_STATUS_LABELS[a.status] ?? a.status : null;
  const decisions = a?.decisions ?? [];
  const primary: Decision | undefined = decisions.includes("approve") ? "approve" : decisions[0];
  const deadline = item.deadline_at ? fmt.date(item.deadline_at, "medium") : null;
  const late = item.deadline_at ? new Date(item.deadline_at).getTime() < now : false;

  return (
    <article className={`brf-m__card${overdue ? " brf-m__card--overdue" : ""}${done ? " brf-m__card--done" : ""}`}
             aria-labelledby={`brf-m-t-${item.kind}-${item.item_no}`}>
      <div className="brf-m__top">
        <span className="brf-m__no" aria-hidden="true">{item.item_no}</span>
        {overdue && <span className="brf-m__pill brf-m__pill--bad">Overdue</span>}
        {status && <span className={`brf-m__pill${a?.status === "proposed" ? " brf-m__pill--gold" : ""}`}>{status}</span>}
        {item.confidence !== null && <span className="brf-m__pill">{Math.round(item.confidence * 100)}% sure</span>}
      </div>
      <h2 className="brf-m__title" id={`brf-m-t-${item.kind}-${item.item_no}`}>{item.title}</h2>
      {item.impact_minor !== null && (
        <div className="brf-m__impact">
          {fmt.money(item.impact_minor, { minor: true, currency: item.currency })}
          {item.impact_estimated && <small className="brf-m__est"> est.</small>}
        </div>
      )}
      <div className="brf-m__meta">
        {deadline && <span className={late ? "brf-m__err" : undefined}>{late ? "Was due" : "By"} {deadline}</span>}
        {item.responsible_label && <span>Owner: {item.responsible_label}</span>}
        {item.recommended_action?.label && <span>Suggested: {item.recommended_action.label}</span>}
      </div>
      {done && <p className="brf-m__ok" role="status">{done}.</p>}
      {error && <p className="brf-m__err" role="alert">{error}</p>}
      {!a && item.action_id !== null && (
        <button type="button" className="brf-m__btn brf-m__btn--quiet" onClick={() => onOpenConsole(item.action_id)}>Open in Clefs</button>
      )}
      {a && !a.can_decide && <p className="brf-m__small">Your role can follow this item but not decide it.</p>}
      {decisions.length > 0 && (
        <div className="brf-m__btns">
          {decisions.map(d => (
            <button key={d} type="button" disabled={busy !== null} aria-expanded={d === step || undefined}
                    className={`brf-m__btn${d === primary ? " brf-m__btn--primary brf-m__btn--wide" : ""}${d === "ignore" ? " brf-m__btn--quiet" : ""}`}
                    onClick={() => tap(d)}>
              {busy === d ? "Working..." : LABEL[d]}
            </button>
          ))}
        </div>
      )}
      {a && step && (
        <StepForm step={step} a={a} busy={busy} amount={amount} setAmount={setAmount} note={note} setNote={setNote}
                  onSubmit={decide} onCancel={() => setStep(null)} />
      )}
    </article>
  );
}

// ─── The inline second step ──────────────────────────────────────────────

function StepForm({ step, a, busy, amount, setAmount, note, setNote, onSubmit, onCancel }: {
  step: Exclude<Step, null>;
  a: MobileAction;
  busy: Decision | null;
  amount: string;
  setAmount: (v: string) => void;
  note: string;
  setNote: (v: string) => void;
  onSubmit: (body: MobileDecideBody) => void;
  onCancel: () => void;
}) {
  const cancel = <button type="button" className="brf-m__btn brf-m__btn--quiet" onClick={onCancel} disabled={busy !== null}>Cancel</button>;

  if (step === "snooze") {
    return (
      <div className="brf-m__form" role="group" aria-label="Snooze for">
        <label>Bring it back in</label>
        <div className="brf-m__btns">
          <button type="button" className="brf-m__btn" disabled={busy !== null} onClick={() => onSubmit({ decision: "snooze", snooze_days: 1 })}>1 day</button>
          <button type="button" className="brf-m__btn" disabled={busy !== null} onClick={() => onSubmit({ decision: "snooze", snooze_days: 7 })}>7 days</button>
        </div>
        {cancel}
      </div>
    );
  }

  if (step === "ignore") {
    return (
      <div className="brf-m__form" role="group" aria-label="Why ignore it">
        <label>Why ignore it? This tunes what Clefs shows you.</label>
        <div className="brf-m__btns">
          {IGNORE_REASONS.map(([k, label]) => (
            <button key={k} type="button" className="brf-m__btn" disabled={busy !== null}
                    onClick={() => onSubmit({ decision: "ignore", note: `ignore-${k}: ${label}` })}>{label}</button>
          ))}
        </div>
        {cancel}
      </div>
    );
  }

  if (step === "adjust" && a.adjust) {
    const b = a.adjust;
    const id = `brf-m-adj-${a.id}`;
    return (
      <form className="brf-m__form" onSubmit={e => { e.preventDefault(); if (amount.trim()) onSubmit({ decision: "adjust", params: { [b.field]: amount.trim().replace(/,/g, "") } }); }}>
        <label htmlFor={id}>New {b.label} ({b.currency}), between {b.min} and {b.max}</label>
        <input id={id} className="brf-m__input" inputMode="decimal" autoComplete="off" autoFocus value={amount}
               onChange={e => setAmount(e.target.value)} placeholder={b.min} />
        <div className="brf-m__btns">
          <button type="submit" className="brf-m__btn brf-m__btn--primary" disabled={busy !== null || !amount.trim()}>
            {busy === "adjust" ? "Working..." : "Approve with this"}
          </button>
          {cancel}
        </div>
      </form>
    );
  }

  if (step === "dispute") {
    const id = `brf-m-dsp-${a.id}`;
    return (
      <form className="brf-m__form" onSubmit={e => { e.preventDefault(); if (note.trim()) onSubmit({ decision: "dispute", note: note.trim() }); }}>
        <label htmlFor={id}>What is wrong with it? Clefs builds the dispute from this.</label>
        <textarea id={id} className="brf-m__input" rows={3} maxLength={500} value={note} onChange={e => setNote(e.target.value)} />
        <div className="brf-m__btns">
          <button type="submit" className="brf-m__btn brf-m__btn--primary" disabled={busy !== null || !note.trim()}>
            {busy === "dispute" ? "Working..." : "Submit dispute"}
          </button>
          {cancel}
        </div>
      </form>
    );
  }

  return null;
}
