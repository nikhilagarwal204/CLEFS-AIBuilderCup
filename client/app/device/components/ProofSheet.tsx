"use client";

/* "Mark as Done" with proof (05 spec, decision 8): the photo the worker
   just took, shrunk on the phone (lib/proofImage.ts), goes to the proof
   route and Gemini checks it against the request inside that call.

   - match: "Photo verified · job closed", and the card leaves the queue;
   - mismatch: "Photo doesn't match: <reason>" and Retake. The job stays
     open and the server has already told the supervisor;
   - undecided (Gemini could not tell, or was unreachable): the reason, and
     the photo goes to the supervisor; Retake stays possible;
   - no connection or a server error: Try again (same photo) or Retake.

   DeviceApp owns the hidden camera input, because a file picker opens only
   from the tap itself; Retake calls back into it from this sheet's button. */

import { useEffect } from "react";
import { LuAlertTriangle, LuCamera, LuCheckCircle2, LuLoader2, LuXCircle } from "react-icons/lu";
import { Button } from "../../ui";
import type { Card } from "../lib/deviceApi";
import { jobTitle } from "../lib/cardView";
import { useStaffI18n } from "./StaffI18n";

export type ProofPhase = "preparing" | "checking" | "verified" | "rejected" | "review" | "error";

export interface ProofView {
  card: Card;
  phase: ProofPhase;
  /** Object URL of the shrunk photo (DeviceApp revokes it). */
  previewUrl: string | null;
  /** The mismatch reason from the check. */
  reason?: string | null;
  /** A ready-to-read error line. */
  error?: string | null;
  /** Whether "Try again" can resend the same photo. */
  canRetry?: boolean;
}

export function ProofSheet({ view, onRetake, onRetry, onClose }: {
  view: ProofView;
  onRetake: () => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  const { t } = useStaffI18n();
  const busy = view.phase === "preparing" || view.phase === "checking";
  const c = view.card;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || busy) return;
      e.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const verdict = (() => {
    switch (view.phase) {
      case "preparing": return { tone: "wait", icon: <LuLoader2 className="svc-dv-spin" aria-hidden="true" />, text: t("proof.preparing") };
      case "checking": return { tone: "wait", icon: <LuLoader2 className="svc-dv-spin" aria-hidden="true" />, text: t("proof.checking") };
      case "verified": return { tone: "ok", icon: <LuCheckCircle2 aria-hidden="true" />, text: t("proof.verified") };
      case "rejected": return {
        tone: "bad", icon: <LuXCircle aria-hidden="true" />,
        text: view.reason ? t("proof.rejected", { reason: view.reason }) : t("proof.rejectedPlain"),
      };
      case "review": return { tone: "wait", icon: <LuAlertTriangle aria-hidden="true" />, text: view.reason || t("proof.review") };
      default: return { tone: "bad", icon: <LuAlertTriangle aria-hidden="true" />, text: view.error || t("proof.offline") };
    }
  })();

  return (
    <div className="svc-dv-scrim" role="presentation" onClick={e => { if (!busy && e.target === e.currentTarget) onClose(); }}>
      <div className="svc-dv-sheet" role="dialog" aria-modal="true" aria-labelledby="svc-dv-proof-title" aria-busy={busy || undefined}>
        <div className="svc-dv-sheet__head">
          <h2 id="svc-dv-proof-title" className="svc-dv-sheet__title">
            {t("proof.title")}
            <span className="svc-dv-sheet__sub" dir="auto">
              {c.room ? t("job.room", { room: c.room }) : t("job.lobby")} · {jobTitle(c)}
            </span>
          </h2>
          {!busy && <button type="button" className="svc-dv-iconbtn" onClick={onClose}>{t("action.close")}</button>}
        </div>

        <div className="svc-dv-photo">
          {view.previewUrl
            // A blob: preview of the photo just taken; next/image has nothing to optimise here.
            // eslint-disable-next-line @next/next/no-img-element
            ? <img src={view.previewUrl} alt="" />
            : <span className="svc-dv-photo__empty"><LuCamera aria-hidden="true" /></span>}
          {view.phase === "checking" && <span className="svc-dv-scan" aria-hidden="true" />}
        </div>

        <div className={`svc-dv-verdict svc-dv-verdict--${verdict.tone}`} role="status" aria-live="polite">
          {verdict.icon}
          <span dir="auto">{verdict.text}</span>
        </div>
        {view.phase === "rejected" && <p className="svc-dv-hint">{t("proof.supervisor")}</p>}

        {!busy && (
          <div className="svc-dv-actions">
            {view.phase === "verified" && (
              <Button variant="primary" className="svc-dv-wide" onClick={onClose}>{t("action.close")}</Button>
            )}
            {(view.phase === "rejected" || view.phase === "review") && (
              <>
                <Button onClick={onClose}>{t("action.close")}</Button>
                <Button variant={view.phase === "rejected" ? "primary" : "secondary"} icon={<LuCamera aria-hidden="true" />}
                  onClick={onRetake}>
                  {t("action.retake")}
                </Button>
              </>
            )}
            {view.phase === "error" && (
              <>
                {view.canRetry
                  ? <Button variant="primary" onClick={onRetry}>{t("action.tryAgain")}</Button>
                  : <Button onClick={onClose}>{t("action.close")}</Button>}
                <Button icon={<LuCamera aria-hidden="true" />} onClick={onRetake}>{t("action.retake")}</Button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
