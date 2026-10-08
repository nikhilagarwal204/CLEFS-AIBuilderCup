"use client";

/* ═══════════════════════════════════════════════════════════════════════
   RecommendationCard: one rate suggestion for one night and room type.
   Shows the move from BAR, "Why this matters" (written from structured
   facts only, never a causal promise), the driving events, the levers and
   the estimated opportunity. Approve keeps the suggestion; moving the
   AdjustSlider turns it into Adjust at the chosen rate; Reject closes it.
   Decisions go through POST /recommendations/{id}/decide, which also
   moves the linked action, so the Brief and WhatsApp see the same state.
   Nothing is pushed to a channel manager: the owner applies the rate.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { ApiError, api } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, ConfidencePill, Input, useToast } from "../../../../../ui";
import AdjustSlider from "./AdjustSlider";
import { decideBody, leverNote, num, REC_STATUS_LABEL, REC_STATUS_TONE, signedPct, sliderBounds } from "../_lib/model";
import { drApi, errorText, PERM } from "../_lib/paths";
import type { Recommendation } from "../_lib/types";

export default function RecommendationCard({
  slug, rec, eventTitles, onDecided, compact = false,
}: {
  slug: string;
  rec: Recommendation;
  /** event_id -> title for the "driven by" line. */
  eventTitles?: Record<string, string>;
  onDecided: (updated: Recommendation | null) => void;
  /** Top 5 list: hide the slider until the owner opens it. */
  compact?: boolean;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const bounds = sliderBounds(rec);
  const [value, setValue] = useState<number | null>(bounds?.value ?? null);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [open, setOpen] = useState(!compact);
  const money = (v: string | number | null) => fmt.money(v, { maximumFractionDigits: 0, currency: rec.currency });

  const decidable = rec.status === "PROPOSED" && can(PERM.manage);
  const proposed = num(rec.recommended);
  const moved = value !== null && proposed !== null && value !== proposed;
  const drivers = rec.driver_event_ids.map(id => eventTitles?.[id]).filter(Boolean) as string[];

  const decide = async (verb: "approve" | "reject") => {
    setBusy(verb);
    try {
      const body = decideBody(rec, verb, value, leverNote(rec.levers, picked, note));
      const updated = await api.post<Recommendation>(drApi(slug, `/recommendations/${rec.id}/decide`), body);
      toast.success(
        body.verb === "reject" ? "Suggestion rejected" : body.verb === "adjust" ? `Saved at ${money(value)}` : `Approved at ${money(rec.recommended)}`,
        body.verb === "reject" ? undefined : "Update the rate in your channel manager; Clefs checks it on the next BAR import.",
      );
      onDecided(updated);
    } catch (err) {
      toast.error("Could not save the decision", errorText(err));
      // Someone decided it elsewhere (Brief, WhatsApp): show the real state.
      if (err instanceof ApiError && err.code === "recommendation_closed") onDecided(null);
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="dr-rec" aria-label={`Rate suggestion for ${rec.room_type_code}`}>
      <div className="dr-rec__head">
        <span className="cx-row" style={{ gap: 6 }}>
          <Badge tone={REC_STATUS_TONE[rec.status]}>{REC_STATUS_LABEL[rec.status]}</Badge>
          <span className="dr-small dr-muted">{rec.room_type_code}, {fmt.date(rec.stay_date, "medium")}</span>
        </span>
        <ConfidencePill value={rec.confidence} />
      </div>

      <div className="dr-rec__rates">
        <span className="dr-rec__now">BAR {money(rec.current_bar)}</span>
        <span className="dr-rec__to">{money(rec.decided ?? rec.recommended)}</span>
        <Badge tone={(num(rec.change_pct) ?? 0) >= 0 ? "gold" : "info"}>{signedPct(rec.change_pct)}</Badge>
        {rec.impact && num(rec.impact) !== 0 && (
          <span className="dr-small dr-muted" title="Rooms likely sold x rate change, after expected commission">
            {rec.impact_label}: <b>{money(rec.impact)}</b>
          </span>
        )}
      </div>

      <div>
        <div className="dr-section-title">Why this matters</div>
        <p className="dr-rec__why">{rec.why || rec.rationale || "No explanation stored for this suggestion."}</p>
        {drivers.length > 0 && <p className="dr-note" style={{ marginTop: 4 }}>Driven by {drivers.join(", ")}.</p>}
        <p className="dr-note" style={{ marginTop: 4 }}>
          {rec.why_source === "llm" ? "Written by AI from the facts shown here. " : ""}
          Demand index {rec.demand_index ?? "-"}. Floor {money(rec.floor)}, ceiling {money(rec.ceiling)}.
        </p>
      </div>

      {rec.status !== "PROPOSED" && (
        <p className="dr-note">
          {rec.decided_at ? `Decided ${fmt.dateTime(rec.decided_at)}. ` : ""}
          {rec.status_reason ?? ""}
        </p>
      )}

      {decidable && (!open || !bounds) && (
        <div className="dr-rec__actions">
          <Button size="sm" variant="primary" loading={busy === "approve"} onClick={() => decide("approve")}>Approve {money(rec.recommended)}</Button>
          {bounds && <Button size="sm" onClick={() => setOpen(true)}>Adjust</Button>}
          <Button size="sm" variant="ghost" loading={busy === "reject"} onClick={() => decide("reject")}>Reject</Button>
        </div>
      )}

      {decidable && open && bounds && value !== null && (
        <>
          <AdjustSlider floor={bounds.min} ceiling={bounds.max} value={value} bar={rec.current_bar} proposed={proposed}
                        onChange={setValue} disabled={busy !== null} currency={rec.currency} />
          {rec.levers.length > 0 && (
            <fieldset className="dr-checks" style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="dr-section-title">Levers (you apply these in your channel manager)</legend>
              {rec.levers.map(l => (
                <label key={l.code} className="dr-check">
                  <input type="checkbox" checked={picked.has(l.code)} disabled={busy !== null}
                         onChange={e => setPicked(prev => {
                           const next = new Set(prev);
                           if (e.target.checked) next.add(l.code); else next.delete(l.code);
                           return next;
                         })} />
                  <span>{l.label}{l.rooms ? ` (${l.rooms} rooms)` : ""}</span>
                </label>
              ))}
            </fieldset>
          )}
          <Input placeholder="Note (optional)" value={note} maxLength={200} disabled={busy !== null}
                 onChange={e => setNote(e.target.value)} aria-label="Decision note" />
          <div className="dr-rec__actions">
            <Button size="sm" variant="primary" loading={busy === "approve"} disabled={busy === "reject"} onClick={() => decide("approve")}>
              {moved ? `Adjust to ${money(value)}` : `Approve ${money(rec.recommended)}`}
            </Button>
            {moved && <Button size="sm" variant="ghost" onClick={() => setValue(bounds.value)}>Back to suggestion</Button>}
            <Button size="sm" variant="ghost" loading={busy === "reject"} disabled={busy === "approve"} onClick={() => decide("reject")}>Reject</Button>
          </div>
        </>
      )}
    </article>
  );
}
