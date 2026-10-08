"use client";

/* LEARN card (03 spec 1.5): a complaint, or a guest's "not resolved", opens
   a recovery case and the device walks the person through Listen,
   Empathize, Apologize, Resolve, Notify. The ticks are stored in the case's
   `learn` JSON through the outbox (offline-safe), and on the device itself,
   so they survive a reload even before the ops routes exist. */

import { useEffect, useState } from "react";
import { getRecoveryCase } from "../lib/deviceApi";
import { idbGet, idbSet, KV } from "../lib/idb";

export type Learn = Record<"listen" | "empathize" | "apologize" | "resolve" | "notify", boolean>;

const STEPS: { key: keyof Learn; letter: string; label: string; hint: string }[] = [
  { key: "listen", letter: "L", label: "Listen", hint: "Let the guest finish. Do not interrupt or explain yet." },
  { key: "empathize", letter: "E", label: "Empathize", hint: "Say you understand how this feels for them." },
  { key: "apologize", letter: "A", label: "Apologize", hint: "Apologize sincerely, whoever caused it. Promise nothing you cannot give." },
  { key: "resolve", letter: "R", label: "Resolve", hint: "Fix it now, or offer a gesture within your authority. Above it, call your supervisor." },
  { key: "notify", letter: "N", label: "Notify", hint: "Tell the guest what happens next, by when, and how you will update them." },
];

export const EMPTY_LEARN: Learn = { listen: false, empathize: false, apologize: false, resolve: false, notify: false };

const keyFor = (requestId: number) => `learn:${requestId}`;

export function LearnCard({
  requestId, staffId, onQueue,
}: {
  requestId: number;
  staffId: number;
  /** Queue the change in the outbox (case id when the server has one). */
  onQueue: (learn: Learn, caseId: number | null) => void;
}) {
  const [learn, setLearn] = useState<Learn>(EMPTY_LEARN);
  const [caseId, setCaseId] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      const local = await idbGet<Learn>(KV, keyFor(requestId));
      if (live && local) setLearn({ ...EMPTY_LEARN, ...local });
      try {
        const c = await getRecoveryCase(requestId, staffId);
        if (!live || !c) return;
        setCaseId(c.id);
        // The server wins for ticks it has; local ticks not yet synced stay.
        setLearn(prev => ({ ...EMPTY_LEARN, ...prev, ...(c.learn ?? {}) }));
      } catch { /* offline: local ticks only */ }
    })();
    return () => { live = false; };
  }, [requestId, staffId]);

  const toggle = (k: keyof Learn) => {
    const next = { ...learn, [k]: !learn[k] };
    setLearn(next);
    void idbSet(KV, keyFor(requestId), next);
    onQueue(next, caseId);
  };

  const done = STEPS.filter(s => learn[s.key]).length;

  return (
    <div className="svc-dv-learn" aria-label="Service recovery: LEARN">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Service recovery</strong>
        <span className="svc-dv-count">{done}/5</span>
      </div>
      {STEPS.map(s => (
        <label key={s.key} className="svc-dv-learn__row">
          <input type="checkbox" checked={learn[s.key]} onChange={() => toggle(s.key)} />
          <span className="svc-dv-learn__letter" aria-hidden="true">{s.letter}</span>
          <span>
            {s.label}
            <span className="svc-dv-learn__hint">{s.hint}</span>
          </span>
        </label>
      ))}
    </div>
  );
}
