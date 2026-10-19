"use client";

/* Task sheet (03 spec 1.6/1.8): one request, and the few things a person on
   the floor can do with it, in the worker's own language (05 spec). Actions
   follow the server's transition table (state.py); reassign / cancel need a
   manager and are not offered here.

   - Unassigned: "I'll take it" (a self-assign, allowed on a device).
   - Assigned to me: Accept (stops the chime), or Mark as Done.
   - Accepted: Mark as Done, On my way, or Decline (a reason; routing picks again).
   - On the way: Mark as Done, or Hand off (a reason; routing picks again).
   Mark as Done on a job that needs proof opens the camera (DeviceApp's
   photo check); every other tap goes through the outbox, so it works
   offline and replays safely. Reply to guest opens the reply sheet. */

import { useEffect, useState } from "react";
import { LuCheck, LuMessageSquare } from "react-icons/lu";
import { formatTime } from "../../lib/format";
import { Badge, Button, Textarea } from "../../ui";
import { canDo, getRequest, needsRecovery, type Card, type FeedEvent } from "../lib/deviceApi";
import { jobTitle, stateTone } from "../lib/cardView";
import { eventWho } from "../lib/history";
import { agoText, durationText, languageName, stateText, type StaffT } from "../lib/i18n";
import {
  englishText, escalationRole, guestLanguageOf, guestWords, isEmergency, isMine, isPriorityUrgent, jobText, needsProof, proofInReview,
  proofRejected, slaInfo,
} from "../lib/worker";
import { LearnCard, type Learn } from "./LearnCard";
import { useStaffI18n } from "./StaffI18n";

export interface TaskActionOpts { note?: string; resolution_code?: string; self?: boolean }

type Ask = null | "decline" | "handoff" | "note";

export function TaskSheet({
  hotelSlug, card: c, meId, deviceId, pending, now, timezone, locale, onAction, onMarkDone, onReply, onNote, onLearn, onClose, staffName,
}: {
  hotelSlug: string;
  card: Card;
  meId: number;
  deviceId: number | null;
  pending: boolean;
  now: number;
  timezone: string;
  locale: string;
  onAction: (c: Card, action: string, opts?: TaskActionOpts) => void;
  /** Mark as Done: the photo check when the job needs proof, else a plain complete. */
  onMarkDone: (c: Card) => void;
  onReply: (c: Card) => void;
  onNote: (c: Card, body: string) => void;
  onLearn: (c: Card, learn: Learn, caseId: number | null) => void;
  onClose: () => void;
  /** Roster lookup so history names the person, not the device (QA GS-11). */
  staffName?: (id: number) => string | undefined;
}) {
  const { t, lang } = useStaffI18n();
  const [ask, setAsk] = useState<Ask>(null);
  const [text, setText] = useState("");
  const [showOriginal, setShowOriginal] = useState(false);
  const [showEnglish, setShowEnglish] = useState(false);
  const [events, setEvents] = useState<FeedEvent[] | null>(null);

  // The history, refreshed whenever the card's version moves.
  useEffect(() => {
    let live = true;
    getRequest(hotelSlug, c.id, meId)
      .then(r => { if (live) setEvents(r.events ?? []); })
      .catch(() => { /* offline: the sheet works without history */ });
    return () => { live = false; };
  }, [hotelSlug, c.id, c.version, meId]);

  // Escape closes the sheet like the Close button (QA GS-13: desk devices
  // have keyboards). An open decline/handoff/note prompt takes the first
  // Escape so a half-typed note is not lost with the whole sheet.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      if (ask) { setAsk(null); setText(""); } else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ask, onClose]);

  const mine = isMine(c, meId, deviceId);
  const unassigned = c.state === "new" || c.state === "reopened";
  const can = (a: string) => canDo(c.state, a) && (a === "assign" ? unassigned : mine);
  const sla = slaInfo(c, now);
  const guestLang = guestLanguageOf(c);
  const guestLangName = languageName(guestLang, lang);
  const body = jobText(c);
  const original = guestWords(c);
  const english = lang === "en" ? null : englishText(c);
  const showLearn = needsRecovery(c) && (mine || unassigned);
  const escalated = escalationRole(c.escalation_level);

  const confirmAsk = () => {
    const note = text.trim();
    if (ask === "note") { if (note) onNote(c, note); }
    else if (ask) onAction(c, ask, { note: note || undefined });
    setAsk(null);
    setText("");
  };

  // The big button: take it, accept it, then Mark as Done.
  const primary = can("assign") ? { a: "assign", label: t("action.take") }
    : can("ack") ? { a: "ack", label: t("action.accept") }
    : can("complete") ? { a: "complete", label: t("action.markDone") }
    : null;
  const fire = (a: string) => (a === "complete" ? onMarkDone(c) : onAction(c, a, { self: a === "assign" }));

  return (
    <div className="svc-dv-scrim" role="presentation" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="svc-dv-sheet" role="dialog" aria-modal="true" aria-labelledby="svc-dv-task-title">
        <div className="svc-dv-sheet__head">
          <span className="svc-dv-room">{c.room || t("job.lobby")}{c.floor ? <small>{t("job.floor", { floor: c.floor })}</small> : null}</span>
          <h2 id="svc-dv-task-title" className="svc-dv-sheet__title">
            <span dir="auto">{jobTitle(c)}</span>
            <span style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
              {isEmergency(c) && <span className="svc-dv-pill svc-dv-pill--emergency">{t("job.emergency")}</span>}
              {isPriorityUrgent(c) && <span className="svc-dv-pill svc-dv-pill--urgent">{t("job.urgent")}</span>}
              <Badge tone={stateTone(c.state)}>{stateText(c.state, t)}</Badge>
              {sla && <Badge tone={sla.tone}>{t(`sla.${sla.kind}`, { t: durationText(sla.ms, lang) })}</Badge>}
              {guestLangName && guestLang !== lang && <Badge tone="info">{guestLangName}</Badge>}
              {pending && <Badge>{t("job.sending")}</Badge>}
            </span>
          </h2>
          <button type="button" className="svc-dv-iconbtn" onClick={onClose}>{t("action.close")}</button>
        </div>

        {body && (
          <div className="svc-dv-section">
            <div className="svc-dv-text" dir="auto">{body}</div>
            {english && (
              <>
                <button type="button" className="svc-dv-link" style={{ alignSelf: "flex-start" }} aria-expanded={showEnglish}
                  onClick={() => setShowEnglish(v => !v)}>
                  {showEnglish ? t("job.hideEnglish") : t("job.showEnglish")}
                </button>
                {showEnglish && <div className="svc-dv-job__alt" dir="ltr" lang="en">{english}</div>}
              </>
            )}
            {original && (
              <>
                <button type="button" className="svc-dv-link" style={{ alignSelf: "flex-start" }} aria-expanded={showOriginal}
                  onClick={() => setShowOriginal(v => !v)}>
                  {showOriginal ? t("job.hideOriginal") : guestLangName ? t("job.showOriginalLang", { lang: guestLangName }) : t("job.showOriginal")}
                </button>
                {showOriginal && <div className="svc-dv-job__alt" dir="auto" lang={guestLang ?? undefined}>{original}</div>}
              </>
            )}
          </div>
        )}

        {proofRejected(c) && (
          <div className="svc-dv-proofnote" role="status">
            {c.proof_reason ? t("proof.rejected", { reason: c.proof_reason }) : t("proof.rejectedPlain")}
          </div>
        )}
        {proofInReview(c) && <div className="svc-dv-proofnote svc-dv-proofnote--review" role="status">{t("proof.review")}</div>}

        <dl className="svc-dv-kv">
          <dt>{t("sheet.filed")}</dt><dd>{formatTime(c.timestamp, timezone, locale)} ({agoText(c.timestamp || now, now, lang, t)})</dd>
          <dt>{t("sheet.with")}</dt><dd>{c.assigned_to || (c.assigned_device_id ? t("sheet.teamDevice") : t("sheet.nobody"))}</dd>
          {c.eta_minutes ? (<><dt>{t("sheet.eta")}</dt><dd>{t("job.eta", { n: c.eta_minutes })}</dd></>) : null}
          {(c.reopen_count ?? 0) > 0 && (<><dt>{t("sheet.reopened")}</dt><dd>{t("sheet.times", { n: c.reopen_count ?? 0 })}</dd></>)}
          {escalated ? (<><dt>{t("sheet.escalated")}</dt><dd>{t(`esc.${escalated}`)}</dd></>) : null}
        </dl>

        {showLearn && <LearnCard requestId={c.id} staffId={meId} onQueue={(l, id) => onLearn(c, l, id)} />}

        {ask ? (
          <div className="svc-dv-section">
            <h3>{ask === "decline" ? t("sheet.declineQ") : ask === "handoff" ? t("sheet.handoffQ") : t("sheet.noteQ")}</h3>
            <Textarea value={text} onChange={e => setText(e.target.value)} rows={3} autoFocus dir="auto"
              placeholder={ask === "note" ? t("sheet.notePh") : t("sheet.optionalPh")} />
            <div className="svc-dv-actions">
              <Button onClick={() => { setAsk(null); setText(""); }}>{t("action.back")}</Button>
              <Button variant="primary" onClick={confirmAsk} disabled={ask === "note" && !text.trim()}>
                {ask === "decline" ? t("action.decline") : ask === "handoff" ? t("action.handoff") : t("action.saveNote")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="svc-dv-actions">
            {primary && (primary.a === "complete" ? (
              <button type="button" className="svc-dv-done svc-dv-wide" onClick={() => fire("complete")}>
                <LuCheck aria-hidden="true" /> {primary.label}
              </button>
            ) : (
              <Button variant="primary" className="svc-dv-wide" onClick={() => fire(primary.a)}>{primary.label}</Button>
            ))}
            {primary?.a !== "complete" && can("complete") && (
              <Button icon={<LuCheck aria-hidden="true" />} onClick={() => onMarkDone(c)}>{t("action.markDone")}</Button>
            )}
            {can("start") && <Button onClick={() => onAction(c, "start")}>{t("action.onMyWay")}</Button>}
            {mine && !unassigned && (
              <Button icon={<LuMessageSquare aria-hidden="true" />} onClick={() => onReply(c)}>{t("action.reply")}</Button>
            )}
            {can("decline") && <Button onClick={() => setAsk("decline")}>{t("action.decline")}</Button>}
            {can("handoff") && <Button onClick={() => setAsk("handoff")}>{t("action.handoff")}</Button>}
            <Button variant="ghost" onClick={() => setAsk("note")}>{t("action.note")}</Button>
            {!primary && !mine && !unassigned && (
              <p className="svc-dv-wide" style={{ margin: 0, color: "var(--clefs-ink-faint)", fontSize: "0.85rem" }}>
                {t("sheet.someoneElse", { name: c.assigned_to || t("sheet.someone") })}
              </p>
            )}
            {needsProof(c) && mine && can("complete") && (
              <p className="svc-dv-wide svc-dv-hint" style={{ margin: 0 }}>{t("job.photoNeeded")}</p>
            )}
          </div>
        )}

        {events && events.length > 0 && (
          <div className="svc-dv-section">
            <h3>{t("sheet.history")}</h3>
            <ol className="svc-dv-timeline">
              {events.slice(-8).reverse().map(ev => (
                <li key={ev.id}>
                  <time>{ev.at ? formatTime(ev.at, timezone, locale) : ""}</time>
                  <span dir="auto">{eventLine(ev, t, staffName)}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}

function eventLine(ev: FeedEvent, t: StaffT, staffName?: (id: number) => string | undefined): string {
  const who = eventWho(ev, staffName);
  if (ev.kind === "note") return `${who}: ${String((ev.payload as { note?: string } | null)?.note ?? "note")}`;
  if (ev.kind === "escalated") {
    const to = (ev.payload as { to?: string } | null)?.to ?? ev.action ?? null;
    const role = escalationRole(to);
    return t("sheet.escalatedTo", { to: role ? t(`esc.${role}`) : to ?? "?" });
  }
  if (ev.kind === "sla_breach") return ev.action === "ack" ? t("hist.missedAck") : t("hist.missedDone");
  if (ev.kind === "created") return t("hist.filed", { who });
  if (ev.kind === "merged") return t("hist.merged");
  if (ev.to_state) return `${who}: ${stateText(ev.to_state, t)}`;
  return `${who}: ${ev.action ?? ev.kind}`;
}
