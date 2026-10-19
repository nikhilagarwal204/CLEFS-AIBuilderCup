"use client";

/* ═══════════════════════════════════════════════════════════════════════
   JobSentCard: "JOB SENT TO STAFF · SENT", one per job (05 spec 3).

   Guest asked for, Room, Staff member (first name · team), Time taken (a
   live mm:ss from the request until a staff member accepts, then frozen:
   "Accepted in 00:41"). The pill follows the job: Sent, Accepted, On the
   way, Done. Staff replies and gestures the duty manager approved show
   under it, already in the guest's language. Once the job is done (and its
   photo checked), the guest is asked "Is everything OK?": "Not fixed"
   reopens it and tells a manager (POST /public/r/{token}/feedback).

   The logic lives in jobs.ts (tested with node --test); this only renders.
   ═══════════════════════════════════════════════════════════════════════ */

import type { JobCard, TrackerStatus } from "./api";
import { langDir, type TFn } from "./i18n";
import { askedFor, formatClock, jobPhase, staffLine, timeTaken, toMs } from "./jobs";

interface Props {
  t: TFn;
  lang: string;
  job: JobCard;
  status: TrackerStatus | undefined;
  now: number;
  askedAt?: number | null;
  reopened?: boolean;
  /** The guest's last answer to "Is everything OK?" on this page. */
  answer?: "yes" | "no" | null;
  busy?: boolean;
  onAnswer: (fixed: boolean) => void;
}

export default function JobSentCard({ t, lang, job, status, now, askedAt, reopened, answer, busy, onAnswer }: Props) {
  const phase = jobPhase(job, status, reopened);
  const finished = phase === "done" || phase === "confirm" || phase === "cancelled";
  const taken = timeTaken(job, now, askedAt, finished ? toMs(status?.updated_at_ms) : null);
  const clock = formatClock(taken.ms);
  const staff = staffLine(t, job);
  const messages = (status?.messages ?? []).filter((m) => m && typeof m.text === "string" && m.text.trim());

  return (
    <section className="cc-job" dir={langDir(lang)} lang={lang} aria-label={t("job.title")}>
      <div className="cc-job-head">
        <span>{t("job.title")}</span>
        <span className={`cc-job-pill cc-job-pill-${phase}`}>{t(`job.phase.${phase}`)}</span>
      </div>
      <dl className="cc-job-rows">
        <div className="cc-job-row">
          <dt>{t("job.askedFor")}</dt>
          <dd>{askedFor(t, job)}</dd>
        </div>
        {job.room && (
          <div className="cc-job-row">
            <dt>{t("job.room")}</dt>
            <dd dir="ltr">{job.room}</dd>
          </div>
        )}
        <div className="cc-job-row">
          <dt>{t("job.staff")}</dt>
          <dd>{staff ?? t("job.finding")}</dd>
        </div>
        {job.eta_minutes && !finished ? (
          <div className="cc-job-row">
            <dt>{t("job.eta")}</dt>
            <dd>{t("job.etaMinutes", { n: Math.round(job.eta_minutes) })}</dd>
          </div>
        ) : null}
        <div className="cc-job-row">
          <dt>{t("job.timeTaken")}</dt>
          {/* The live clock is not announced every second; the frozen one is read once. */}
          <dd className="cc-job-time" aria-live={taken.frozen ? "polite" : "off"}>
            {taken.frozen ? t("job.acceptedIn", { time: clock }) : <span dir="ltr">{clock}</span>}
          </dd>
        </div>
      </dl>

      {status?.proof_status === "verified" && phase !== "cancelled" && (
        <p className="cc-job-note">{t("job.photoChecked")}</p>
      )}

      {messages.map((m, i) => (
        <p key={`${m.kind}-${i}-${m.at ?? ""}`} className={m.kind === "status" ? "cc-job-note" : "cc-job-msg"}>
          {m.kind === "gesture" ? <b>{t("job.gesture")}</b> : m.kind === "staff_reply" ? <b>{m.staff_first_name || t("job.staffReply")}</b> : null}
          {m.text}
        </p>
      ))}

      {phase === "confirm" ? (
        <div className="cc-job-q" role="group" aria-label={t("job.confirmQ")}>
          <span>{t("job.confirmQ")}</span>
          <button type="button" className="cc-mini" disabled={busy} onClick={() => onAnswer(true)}>{t("job.yes")}</button>
          <button type="button" className="cc-mini cc-mini-alert" disabled={busy} onClick={() => onAnswer(false)}>{t("job.notFixed")}</button>
        </div>
      ) : answer ? (
        <p className="cc-job-note" role="status">{t(answer === "yes" ? "job.thanks" : "job.reopened")}</p>
      ) : null}
    </section>
  );
}
