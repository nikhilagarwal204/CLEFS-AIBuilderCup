"use client";

/* ═══════════════════════════════════════════════════════════════════════
   "Needs your approval" (05 spec 2.12): open recovery cases on the service
   Dashboard, so the duty manager approves a gesture before the guest writes
   the review. A case opens when the guest says a job was not fixed, sounds
   frustrated, or a request never arrived; Gemini suggests a gesture, and the
   manager approves it as is, edits it first, or dismisses it.

   Reads GET /service/recovery-cases?status=open&approval=pending and decides
   through POST /service/recovery-cases/{id}/decide {action: approve|dismiss,
   gesture?} (service/recovery.py decide: an approved gesture reaches the
   guest in their language). Rows are normalised by deskLabels.approvalItems.
   The guest's mood comes from the dashboard's own copy of the request when
   the case does not carry it. A server without the route (404/405) hides
   the panel instead of erroring.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useState } from "react";
import { LuHeartHandshake, LuPencil, LuSparkles } from "react-icons/lu";
import { ApiError } from "../../../../../lib/api";
import { useToast } from "../../../../../ui";
import { approvalItems, clockText, type ApprovalItem, type MoodLookup } from "./deskLabels";
import { ChipView, useNow } from "./RequestMeta";
import { decideRecoveryCase, errorText, fetchRecoveryCases, isMissingRoute, type RecoveryCase } from "./serviceApi";

type Load = "loading" | "ready" | "unavailable" | "error";

function decideError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.code === "above_authority") return "This gesture is above your authority. Ask the GM to approve it, or edit it.";
    if (err.status === 403) return "Only a manager can decide on a recovery gesture.";
    if (err.status === 409) return "Someone already decided on this case.";
    if (err.status === 400 && err.code === "gesture_required") return "Write the gesture the guest will read first.";
  }
  return errorText(err);
}

function ApprovalRow({ item, canManage, busy, onDecide }: {
  item: ApprovalItem;
  canManage: boolean;
  busy: boolean;
  onDecide: (item: ApprovalItem, action: "approve" | "dismiss", gesture?: string) => void;
}) {
  const now = useNow(30_000);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.suggestion);
  const age = item.openedAt && now ? clockText(now - item.openedAt) : null;
  const gesture = (editing ? draft : item.suggestion).trim();

  return (
    <li className="svc-appr__item">
      <div className="svc-appr__top">
        <span className="svc-appr__room">{item.room ? `Room ${item.room}` : "No room"}</span>
        {item.item && <span className="svc-faint">{item.item}</span>}
        <span className={`svc-chip svc-chip--${item.kind === "not_fixed" || item.kind === "never_arrived" ? "bad" : "warn"}`}>
          {item.kindLabel}
        </span>
        <ChipView chip={item.mood} />
        {age && <span className="svc-faint" title="Waiting for a decision">waiting {age}</span>}
      </div>
      {item.summary && <div className="svc-appr__summary" dir="auto">{item.summary}</div>}

      {editing ? (
        <label>
          <span className="svc-appr__label">Gesture to offer</span>
          <textarea className="svc-appr__edit" value={draft} maxLength={500} disabled={busy}
                    onChange={e => setDraft(e.target.value)} aria-label={`Gesture for room ${item.room ?? ""}`} />
        </label>
      ) : (
        <div className="svc-appr__gesture">
          <LuSparkles aria-hidden="true" />
          <div>
            <span className="svc-appr__label">
              {item.suggestionLabel}{item.value ? ` · worth ${item.value}` : ""}
            </span>
            {item.suggestion || <span className="svc-faint">No suggestion yet. Edit to write one.</span>}
          </div>
        </div>
      )}

      {canManage ? (
        <div className="svc-appr__actions">
          <button type="button" className="btn-complete" style={{ padding: "7px 14px", fontSize: "0.82rem" }}
                  disabled={busy || !gesture} onClick={() => onDecide(item, "approve", gesture)}>
            {busy ? "..." : editing ? "Approve edited gesture" : "Approve"}
          </button>
          {editing ? (
            <button type="button" className="btn-ghost" style={{ padding: "7px 14px", fontSize: "0.82rem" }} disabled={busy}
                    onClick={() => { setEditing(false); setDraft(item.suggestion); }}>
              Cancel edit
            </button>
          ) : (
            <button type="button" className="btn-ghost" style={{ padding: "7px 14px", fontSize: "0.82rem" }} disabled={busy}
                    onClick={() => { setDraft(item.suggestion); setEditing(true); }}>
              <LuPencil size={13} aria-hidden="true" /> Edit
            </button>
          )}
          <button type="button" className="btn-ghost" style={{ padding: "7px 14px", fontSize: "0.82rem" }} disabled={busy}
                  onClick={() => onDecide(item, "dismiss")}>
            Dismiss
          </button>
        </div>
      ) : (
        <div className="svc-faint">A manager approves recovery gestures.</div>
      )}
    </li>
  );
}

export default function ApprovalsPanel({ hotelSlug, canManage, refreshKey = 0, moodOf }: {
  hotelSlug: string;
  canManage: boolean;
  /** Bumped by the dashboard on service.recovery_changed (and reconnects). */
  refreshKey?: number;
  /** The guest's mood from the dashboard's copy of the case's request. */
  moodOf?: MoodLookup;
}) {
  const toast = useToast();
  const [cases, setCases] = useState<RecoveryCase[]>([]);
  const [load, setLoad] = useState<Load>("loading");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce(n => n + 1), []);
  // Rows are derived, so the mood fills in when the dashboard's requests arrive.
  const items = useMemo(() => approvalItems(cases, moodOf), [cases, moodOf]);

  useEffect(() => {
    let live = true;
    fetchRecoveryCases(hotelSlug, { status: "open", pendingOnly: true })
      .then(out => {
        if (!live) return;
        setCases(Array.isArray(out) ? out : Array.isArray(out?.items) ? out.items : []);
        setLoad("ready");
      })
      .catch(err => {
        if (!live) return;
        // No route on this server, or a viewer it is closed to: no panel.
        if (isMissingRoute(err) || (err instanceof ApiError && (err.status === 401 || err.status === 403))) setLoad("unavailable");
        else setLoad(prev => (prev === "ready" ? prev : "error"));
      });
    return () => { live = false; };
  }, [hotelSlug, refreshKey, nonce]);

  const decide = async (item: ApprovalItem, action: "approve" | "dismiss", gesture?: string) => {
    setBusyId(item.id);
    try {
      await decideRecoveryCase(hotelSlug, item.id, action === "approve" ? { action, gesture } : { action });
      setCases(prev => prev.filter(c => c.id !== item.id));
      const where = item.room ? ` for room ${item.room}` : "";
      if (action === "approve") toast.success(`Gesture approved${where}`, gesture);
      else toast.info(`Dismissed${where}`);
      reload();
    } catch (err) {
      toast.error("Could not record the decision", decideError(err));
      if (err instanceof ApiError && err.status === 409) reload();
    } finally {
      setBusyId(null);
    }
  };

  if (load === "unavailable") return null;

  return (
    <section className="svc-appr" aria-labelledby="svc-appr-title">
      <div className="svc-appr__head">
        <h2 className="svc-appr__title" id="svc-appr-title">
          <LuHeartHandshake aria-hidden="true" style={{ color: "var(--clefs-gold-400)" }} />
          Needs your approval
          {items.length > 0 && <span className="svc-appr__count" aria-label={`${items.length} waiting`}>{items.length}</span>}
        </h2>
        {load === "error" && (
          <button type="button" className="svc-linkbtn" onClick={() => reload()}>Could not load. Try again</button>
        )}
      </div>
      {load === "loading" && <p className="svc-appr__empty">Loading recovery cases...</p>}
      {load !== "loading" && items.length === 0 && load !== "error" && (
        <p className="svc-appr__empty">
          Nothing waiting. A case appears here when a guest says a job was not fixed, sounds frustrated,
          or a request never arrived, with a recovery gesture suggested for you to approve.
        </p>
      )}
      {items.length > 0 && (
        <ul className="svc-appr__list">
          {items.map(item => (
            <ApprovalRow key={item.id} item={item} canManage={canManage} busy={busyId === item.id} onDecide={decide} />
          ))}
        </ul>
      )}
    </section>
  );
}
