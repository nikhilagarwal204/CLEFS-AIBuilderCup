"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One event row: dates, venue, status, where it came from (each source
   with its confidence), when it was last checked, and the change history
   loaded on demand from GET /events/{id}. Unverified hotel events can be
   confirmed; any event can be dismissed (the relevance model learns from
   it); the hotel's own manual entries can be removed.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, ConfidencePill, useToast } from "../../../../../ui";
import DismissDialog from "./DismissDialog";
import { CATEGORY_LABEL, changeText, SOURCE_LABEL, STATUS_TONE } from "../_lib/model";
import { drApi, errorText, PERM } from "../_lib/paths";
import type { DemandEvent, EventDetail } from "../_lib/types";

function safeUrl(u: string): string | null {
  try {
    const p = new URL(u);
    return p.protocol === "https:" || p.protocol === "http:" ? p.href : null;
  } catch {
    return null;
  }
}

export default function EventItem({
  slug, event, extra, dismissed = false, onChanged,
}: {
  slug: string;
  event: DemandEvent;
  /** Night-specific line (score, distance) from the day view. */
  extra?: string;
  dismissed?: boolean;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const manage = can(PERM.manage);
  const oneDay = event.start_date === event.end_date;
  const dates = oneDay ? fmt.date(event.start_date, "medium") : `${fmt.date(event.start_date, "medium")} to ${fmt.date(event.end_date, "medium")}`;
  const ownManual = event.scope === "hotel" && event.sources.some(s => s.source === "MANUAL" || s.source === "ICS");

  // The day view already carries each event's changes; the events list does not.
  const inline = (event as Partial<EventDetail>).changes;

  const toggleHistory = async () => {
    const next = !showHistory;
    setShowHistory(next);
    if (!next || detail || inline) return;
    setLoadingHistory(true);
    try {
      setDetail(await api.get<EventDetail>(drApi(slug, `/events/${encodeURIComponent(event.id)}`)));
    } catch (err) {
      toast.error("Could not load the history", errorText(err));
      setShowHistory(false);
    } finally {
      setLoadingHistory(false);
    }
  };

  const run = async (what: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(what);
    try {
      await fn();
      toast.success(ok, event.title);
      onChanged();
    } catch (err) {
      toast.error("Could not update the event", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const confirm = () => run("confirm", () => api.post(drApi(slug, `/events/${encodeURIComponent(event.id)}/confirm`)), "Confirmed");
  const undismiss = () => run("undismiss", () => api.del(drApi(slug, `/events/${encodeURIComponent(event.id)}/dismiss`)), "Back in your scores");
  const remove = () => {
    if (typeof window !== "undefined" && !window.confirm(`Remove "${event.title}" from your events?`)) return;
    void run("remove", () => api.del(drApi(slug, `/events/${encodeURIComponent(event.id)}`)), "Removed");
  };
  const changes = inline ?? detail?.changes ?? [];
  const historyReady = Boolean(inline || detail);

  return (
    <li className={dismissed || event.status === "CANCELLED" ? "dr-item dr-item--dim" : "dr-item"}>
      <div className="dr-item__head">
        <div style={{ minWidth: 0 }}>
          <div className="dr-item__title">{event.title}</div>
          <div className="dr-item__meta">
            <span>{CATEGORY_LABEL[event.category] ?? event.category}</span>
            <span>{dates}{event.moon_dependent ? ` (moon sighting, +/- ${event.date_uncertainty_days ?? 1} day)` : ""}</span>
            {event.venue_codes.length > 0 && <span>{event.venue_codes.join(", ")}</span>}
            {event.attendance_est ? <span>about {fmt.number(event.attendance_est)} people</span> : null}
            {extra && <span>{extra}</span>}
          </div>
        </div>
        <div className="dr-chips">
          {event.status !== "SCHEDULED" && <Badge tone={STATUS_TONE[event.status]}>{event.status.toLowerCase()}</Badge>}
          <Badge tone={event.verification === "VERIFIED" ? "ok" : "warn"} dot>
            {event.verification === "VERIFIED" ? "Verified" : "Unverified"}
          </Badge>
          <ConfidencePill value={event.confidence} />
          {dismissed && <Badge>Dismissed</Badge>}
        </div>
      </div>

      {event.status === "RESCHEDULED" && event.original_start && (
        <p className="dr-note" style={{ marginTop: 6 }}>
          Originally {fmt.date(event.original_start, "medium")}
          {event.original_end && event.original_end !== event.original_start ? ` to ${fmt.date(event.original_end, "medium")}` : ""}.
        </p>
      )}

      <div className="dr-item__meta">
        <span>
          Sources: {event.sources.map(s => `${SOURCE_LABEL[s.source] ?? s.source}${s.confidence !== null ? ` (${Math.round(s.confidence * 100)}%)` : ""}`).join(", ") || "none"}
        </span>
        <span>Last checked {event.last_checked_at ? fmt.relative(event.last_checked_at) : "never"}</span>
        {!event.actionable && <span>Not used for pricing until confirmed or corroborated</span>}
        {event.source_urls.slice(0, 2).map(u => {
          const href = safeUrl(u);
          return href ? <a key={u} className="cx-link" href={href} target="_blank" rel="noopener noreferrer">Source link</a> : null;
        })}
      </div>

      <div className="cx-row" style={{ gap: 6, marginTop: 8 }}>
        <Button size="sm" variant="ghost" loading={loadingHistory} onClick={toggleHistory} aria-expanded={showHistory}>
          {showHistory ? "Hide history" : "Change history"}
        </Button>
        {manage && !event.actionable && event.scope === "hotel" && (
          <Button size="sm" loading={busy === "confirm"} onClick={confirm}>Confirm event</Button>
        )}
        {manage && !dismissed && <Button size="sm" variant="ghost" onClick={() => setDismissOpen(true)}>Dismiss</Button>}
        {manage && dismissed && <Button size="sm" variant="ghost" loading={busy === "undismiss"} onClick={undismiss}>Undo dismiss</Button>}
        {manage && ownManual && <Button size="sm" variant="ghost" loading={busy === "remove"} onClick={remove}>Remove</Button>}
      </div>

      {showHistory && historyReady && (
        changes.length ? (
          <ol className="dr-history">
            {changes.map((c, i) => (
              <li key={`${c.at}-${i}`}>
                {fmt.date(c.at, "medium")}: {changeText(c)}
                {c.source ? <span className="dr-faint"> ({SOURCE_LABEL[c.source] ?? c.source})</span> : null}
              </li>
            ))}
          </ol>
        ) : <p className="dr-note" style={{ marginTop: 6 }}>No changes since we first saw it on {fmt.date(event.first_seen_at, "medium")}.</p>
      )}

      {dismissOpen && (
        <DismissDialog slug={slug} event={event} open={dismissOpen} onClose={() => setDismissOpen(false)}
                       onDone={() => onChanged()} />
      )}
    </li>
  );
}
