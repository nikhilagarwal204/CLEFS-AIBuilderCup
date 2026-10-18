"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Dismiss an event, or teach the relevance model a whole class: this event
   only, every event of its category, or its category at its venue. A
   class or venue dismissal lowers w_class for every future match; "mute"
   drops the class to zero. POST /events/{id}/dismiss (demand.manage).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, Modal, Select, useToast } from "../../../../../ui";
import { CATEGORY_LABEL, dismissScopeText, type DismissScope } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { DemandEvent, DismissResult } from "../_lib/types";

const REASONS: readonly { value: string; label: string }[] = [
  { value: "not_relevant", label: "Our guests do not come for this" },
  { value: "too_far", label: "Too far from the hotel" },
  { value: "already_priced", label: "Already in our pricing" },
  { value: "wrong_dates", label: "Dates look wrong" },
  { value: "mute", label: "Never show this kind again (mute)" },
];

export default function DismissDialog({
  slug, event, open, onClose, onDone,
}: {
  slug: string;
  event: Pick<DemandEvent, "id" | "title" | "category" | "venue_codes">;
  open: boolean;
  onClose: () => void;
  onDone: (r: DismissResult) => void;
}) {
  const toast = useToast();
  const [scope, setScope] = useState<DismissScope>("event");
  const [reason, setReason] = useState("not_relevant");
  const [busy, setBusy] = useState(false);
  const venue = event.venue_codes[0] ?? null;
  // Mute only means something for a class; for one event it is a dismissal.
  const muteBlocked = reason === "mute" && scope === "event";

  const submit = async () => {
    setBusy(true);
    try {
      const r = await api.post<DismissResult>(drApi(slug, `/events/${encodeURIComponent(event.id)}/dismiss`), { scope, reason });
      toast.success("Dismissed", scope === "event" ? event.title : dismissScopeText(scope, event.category, venue));
      onDone(r);
      onClose();
    } catch (err) {
      toast.error("Could not dismiss", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!busy}
      size="sm"
      title={`Dismiss ${event.title}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={muteBlocked} onClick={submit}>Dismiss</Button>
        </>
      }
    >
      <div className="cx-stack" style={{ gap: 12 }}>
        <fieldset className="dr-checks" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="dr-section-title">What should count for less</legend>
          {(["event", "class", "venue"] as const).map(s => (
            <label key={s} className="dr-check">
              <input type="radio" name="dr-dismiss-scope" checked={scope === s} disabled={s === "venue" && !venue}
                     onChange={() => setScope(s)} />
              <span>
                {s === "event" ? "Only this event" : s === "class" ? `All ${CATEGORY_LABEL[event.category].toLowerCase()} events` : `${CATEGORY_LABEL[event.category]} events at ${venue ?? "this venue"}`}
                <span className="dr-faint dr-small"> {s === "event" ? "" : dismissScopeText(s, event.category, venue)}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <Field label="Why" hint={muteBlocked ? "Mute needs a class or venue: pick one above." : "This teaches the relevance score; nothing is deleted."}>
          <Select value={reason} onChange={e => setReason(e.target.value)}>
            {REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}
