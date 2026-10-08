"use client";

/* Closing an inquiry always records why (4.10): the lost-reason mix is a
   metric the owner reads, so there is no reasonless "archive".
   - Lost: a reason a person can know (the automatic ones, such as slow
     response or booked through an OTA, are set by the system);
   - Turned down: the hotel said no (no rooms, displacement, policy, floor);
   - Future: dates changed, with a remind date after today;
   - Spam. */

import { useState } from "react";
import { api } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Button, Field, Input, Modal, Segmented, Textarea, useToast } from "../../../../ui";
import { errorText, inqPath, LOST_PICKABLE, reasonLabel, TURNDOWN_REASONS } from "./lib";
import type { InquiryDetail } from "./types";

type Mode = "lost" | "decline" | "future" | "spam";

const MODES = [
  { value: "lost" as Mode, label: "Lost" },
  { value: "decline" as Mode, label: "Turn down" },
  { value: "future" as Mode, label: "Future" },
  { value: "spam" as Mode, label: "Spam" },
];

export default function ClosePicker({ row, open, onClose, onDone }: {
  row: InquiryDetail;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("lost");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [remindOn, setRemindOn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tomorrow in the hotel's timezone: the server refuses today or earlier.
  const today = fmt.dayKey(new Date());
  const tomorrow = new Date(Date.parse(`${today}T12:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
  const reasons: readonly string[] = mode === "lost" ? LOST_PICKABLE : mode === "decline" ? TURNDOWN_REASONS : [];
  const ready = mode === "spam" || (mode === "future" ? remindOn >= tomorrow : reasons.includes(reason));

  function pick(m: Mode) {
    setMode(m);
    setReason("");
    setError(null);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const body = mode === "spam" ? undefined
        : mode === "future" ? { remind_on: remindOn, note: note.trim() || undefined }
        : { reason, note: note.trim() || undefined };
      await api.post(inqPath(slug, `/${row.id}/${mode}`), body);
      toast.success(mode === "future" ? "Moved to future" : mode === "spam" ? "Marked as spam" : "Inquiry closed");
      setNote("");
      setReason("");
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} dismissible={!busy} title={`Close ${row.ref.toUpperCase()}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant={mode === "spam" ? "danger" : "primary"} loading={busy} disabled={!ready} onClick={submit}>
            {mode === "future" ? "Remind me later" : mode === "spam" ? "Mark as spam" : "Close inquiry"}
          </Button>
        </>
      }>
      <div style={{ display: "grid", gap: 14 }}>
        <Segmented options={MODES} value={mode} onChange={pick} ariaLabel="How it ended" />
        {reasons.length > 0 && (
          <fieldset className="inq-radio" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="inq-muted" style={{ marginBottom: 6 }}>
              {mode === "lost" ? "Why did the guest not book?" : "Why is the hotel saying no?"}
            </legend>
            {reasons.map(r => (
              <label key={r}>
                <input type="radio" name="inq-close-reason" value={r} checked={reason === r} onChange={() => setReason(r)} />
                {reasonLabel(r)}
              </label>
            ))}
          </fieldset>
        )}
        {mode === "future" && (
          <Field label="Remind on" hint="The inquiry comes back to the list on this date.">
            <Input type="date" min={tomorrow} value={remindOn} onChange={e => setRemindOn(e.target.value)} />
          </Field>
        )}
        {mode === "spam" && (
          <p className="inq-muted" style={{ margin: 0 }}>
            Spam never reopens and is removed after a year. Use Lost with &ldquo;Spam or duplicate&rdquo; for a real guest who wrote twice.
          </p>
        )}
        {mode !== "spam" && (
          <Field label="Note (optional)">
            <Textarea rows={2} maxLength={2000} value={note} onChange={e => setNote(e.target.value)} />
          </Field>
        )}
        {error && <p className="inq-note inq-note--bad" role="alert">{error}</p>}
      </div>
    </Modal>
  );
}
