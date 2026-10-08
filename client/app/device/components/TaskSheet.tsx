"use client";

/* Task sheet (03 spec 1.6/1.8): one request, and the few things a person on
   the floor can do with it. Actions follow the server's transition table
   (state.py); reassign / cancel need a manager and are not offered here.

   - Unassigned: "I'll take it" (a self-assign, allowed on a device).
   - Assigned to me: Acknowledge (stops the chime), or Complete (an implicit ack).
   - Acknowledged: On my way, Complete, or Decline (a reason; routing picks again).
   - On the way: Complete, or Hand off (a reason; routing picks again).
   Every tap goes through the outbox, so it works offline and replays safely. */

import { useEffect, useState } from "react";
import { formatTime } from "../../lib/format";
import { Badge, Button, Textarea } from "../../ui";
import { canDo, getRequest, isUrgent, needsRecovery, type Card, type FeedEvent } from "../lib/deviceApi";
import {
  ageText, cardTitle, descriptionOf, hasOriginal, languageLabel, roomLabel, slaText, stateLabel, stateTone,
} from "../lib/cardView";
import { eventWho } from "../lib/history";
import { LearnCard, type Learn } from "./LearnCard";

export interface TaskActionOpts { note?: string; resolution_code?: string; self?: boolean }

type Ask = null | "decline" | "handoff" | "note";

export function TaskSheet({
  hotelSlug, card: c, meId, deviceId, pending, now, timezone, locale, onAction, onNote, onLearn, onClose, staffName,
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
  onNote: (c: Card, body: string) => void;
  onLearn: (c: Card, learn: Learn, caseId: number | null) => void;
  onClose: () => void;
  /** Roster lookup so history names the person, not the device (QA GS-11). */
  staffName?: (id: number) => string | undefined;
}) {
  const [ask, setAsk] = useState<Ask>(null);
  const [text, setText] = useState("");
  const [showOriginal, setShowOriginal] = useState(false);
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

  const mine = c.assigned_staff_id === meId || (deviceId != null && c.assigned_device_id === deviceId && c.assigned_staff_id == null);
  const unassigned = c.state === "new" || c.state === "reopened";
  const can = (a: string) => canDo(c.state, a) && (a === "assign" ? unassigned : mine);
  const sla = slaText(c, now);
  const lang = languageLabel(c.language);
  const showLearn = needsRecovery(c) && (mine || unassigned);

  const confirmAsk = () => {
    const note = text.trim();
    if (ask === "note") { if (note) onNote(c, note); }
    else if (ask) onAction(c, ask, { note: note || undefined });
    setAsk(null);
    setText("");
  };

  const primary = can("assign") ? { a: "assign", label: "I'll take it" }
    : can("ack") ? { a: "ack", label: "Acknowledge" }
    : can("start") ? { a: "start", label: "On my way" }
    : can("complete") ? { a: "complete", label: "Done" }
    : null;

  return (
    <div className="svc-dv-scrim" role="presentation" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="svc-dv-sheet" role="dialog" aria-modal="true" aria-labelledby="svc-dv-task-title">
        <div className="svc-dv-sheet__head">
          <span className="svc-dv-room">{roomLabel(c)}{c.floor ? <small>Floor {c.floor}</small> : null}</span>
          <h2 id="svc-dv-task-title" className="svc-dv-sheet__title">
            {cardTitle(c)}
            <span style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
              <Badge tone={stateTone(c.state)}>{stateLabel(c.state)}</Badge>
              {isUrgent(c) && <Badge tone="bad" dot>{c.sensitivity === "emergency" ? "Emergency" : "Urgent"}</Badge>}
              {sla && <Badge tone={sla.tone}>{sla.text}</Badge>}
              {lang && <Badge tone="info">{lang}</Badge>}
              {pending && <Badge>Sending...</Badge>}
            </span>
          </h2>
          <button type="button" className="svc-dv-iconbtn" onClick={onClose} aria-label="Close">Close</button>
        </div>

        {descriptionOf(c) && (
          <div className="svc-dv-section">
            <div className="svc-dv-text">{descriptionOf(c)}</div>
            {hasOriginal(c) && (
              <>
                <button type="button" className="svc-dv-link" style={{ alignSelf: "flex-start" }} onClick={() => setShowOriginal(v => !v)}>
                  {showOriginal ? "Hide the guest's words" : `Show the guest's words${lang ? ` (${lang})` : ""}`}
                </button>
                {showOriginal && <div className="svc-dv-text" dir="auto" style={{ opacity: 0.85 }}>{c.original_text}</div>}
              </>
            )}
          </div>
        )}

        <dl className="svc-dv-kv">
          <dt>Filed</dt><dd>{formatTime(c.timestamp, timezone, locale)} ({ageText(c, now)})</dd>
          <dt>With</dt><dd>{c.assigned_to || (c.assigned_device_id ? "This team's device" : "Nobody yet")}</dd>
          {(c.reopen_count ?? 0) > 0 && (<><dt>Reopened</dt><dd>{c.reopen_count} time(s)</dd></>)}
          {c.escalation_level ? (<><dt>Escalated</dt><dd>Level {c.escalation_level}</dd></>) : null}
        </dl>

        {showLearn && <LearnCard requestId={c.id} staffId={meId} onQueue={(l, id) => onLearn(c, l, id)} />}

        {ask ? (
          <div className="svc-dv-section">
            <h3>{ask === "decline" ? "Why are you declining?" : ask === "handoff" ? "Hand-off note for the next person" : "Add a note"}</h3>
            <Textarea value={text} onChange={e => setText(e.target.value)} rows={3} autoFocus
              placeholder={ask === "note" ? "What should the team know?" : "Optional, but it helps the next person"} />
            <div className="svc-dv-actions">
              <Button onClick={() => { setAsk(null); setText(""); }}>Back</Button>
              <Button variant="primary" onClick={confirmAsk} disabled={ask === "note" && !text.trim()}>
                {ask === "decline" ? "Decline" : ask === "handoff" ? "Hand off" : "Save note"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="svc-dv-actions">
            {primary && (
              <Button variant="primary" className="svc-dv-wide" onClick={() => onAction(c, primary.a, { self: primary.a === "assign" })}>
                {primary.label}
              </Button>
            )}
            {primary?.a !== "complete" && can("complete") && <Button onClick={() => onAction(c, "complete")}>Done</Button>}
            {can("decline") && <Button onClick={() => setAsk("decline")}>Decline</Button>}
            {can("handoff") && <Button onClick={() => setAsk("handoff")}>Hand off</Button>}
            <Button variant="ghost" onClick={() => setAsk("note")}>Add note</Button>
            {!primary && !mine && !unassigned && (
              <p className="svc-dv-wide" style={{ margin: 0, color: "var(--clefs-ink-faint)", fontSize: "0.85rem" }}>
                This job is with {c.assigned_to || "someone else"}. A supervisor can reassign it from the console.
              </p>
            )}
          </div>
        )}

        {events && events.length > 0 && (
          <div className="svc-dv-section">
            <h3>History</h3>
            <ol className="svc-dv-timeline">
              {events.slice(-8).reverse().map(ev => (
                <li key={ev.id}>
                  <time>{ev.at ? formatTime(ev.at, timezone, locale) : ""}</time>
                  <span>{eventLine(ev, staffName)}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}

function eventLine(ev: FeedEvent, staffName?: (id: number) => string | undefined): string {
  const who = eventWho(ev, staffName);
  if (ev.kind === "note") return `${who}: ${String((ev.payload as { note?: string } | null)?.note ?? "note")}`;
  if (ev.kind === "escalated") return `Escalated to ${String((ev.payload as { to?: string } | null)?.to ?? ev.action ?? "a manager")}`;
  if (ev.kind === "sla_breach") return ev.action === "ack" ? "Missed the response time" : "Missed the completion time";
  if (ev.kind === "created") return `Filed by ${who}`;
  if (ev.kind === "merged") return "Guest asked again (merged)";
  if (ev.to_state) return `${who}: ${stateLabel(ev.to_state)}`;
  return `${who}: ${ev.action ?? ev.kind}`;
}
