"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Answer feedback: helpful / wrong, with an optional note on "wrong". It
   lands on the answer's ask_logs row, where owners and GMs review the
   team's misses (GET /ask/logs?all=true&feedback=-1) and the eval set gets
   its next items. A thumbs-down sends at once, so a hurried owner still
   leaves the signal; the note is a second, optional step.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuThumbsDown, LuThumbsUp } from "react-icons/lu";
import { api } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { Button, Textarea, useToast } from "../../../../ui";
import { askApi, askErrorText } from "./lib";
import type { LogRow } from "./types";

type Value = 1 | -1;

export default function Feedback({ logId, initial, initialNote, onSaved }: {
  logId: number;
  initial?: Value | null;
  initialNote?: string | null;
  onSaved?: (value: Value, note: string | null) => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [value, setValue] = useState<Value | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState(initialNote ?? "");
  const [noteSent, setNoteSent] = useState(!!initialNote);

  async function send(v: Value, withNote?: string) {
    setBusy(true);
    try {
      const row = await api.post<LogRow>(askApi(slug, `/${logId}/feedback`), {
        value: v,
        note: withNote?.trim() ? withNote.trim().slice(0, 1000) : undefined,
      });
      setValue(v);
      onSaved?.(v, row?.feedback_note ?? null);
      if (withNote !== undefined) {
        setNoteSent(true);
        setNoteOpen(false);
        toast.success("Thanks, noted", "The team can review answers marked wrong.");
      } else if (v === -1) {
        setNoteOpen(true);
      }
    } catch (err) {
      toast.error("Feedback not saved", askErrorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ask-fb" role="group" aria-label="Was this answer right?">
      <Button size="sm" variant="ghost" className="ask-fb__btn" aria-pressed={value === 1} disabled={busy}
              icon={<LuThumbsUp />} onClick={() => send(1)} title="Helpful">
        <span className="cx-sr">Helpful</span>
      </Button>
      <Button size="sm" variant="ghost" className="ask-fb__btn" aria-pressed={value === -1} disabled={busy}
              icon={<LuThumbsDown />} onClick={() => (value === -1 ? setNoteOpen(o => !o) : send(-1))}
              title="Wrong or unhelpful">
        <span className="cx-sr">Wrong or unhelpful</span>
      </Button>
      {noteOpen && (
        <form className="ask-fb__form" onSubmit={e => { e.preventDefault(); void send(-1, note); }}>
          <Textarea
            aria-label="What was wrong?"
            placeholder={noteSent ? "Update your note" : "What was wrong? (optional)"}
            rows={2}
            maxLength={1000}
            value={note}
            onChange={e => setNote(e.target.value)}
          />
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            <Button size="sm" variant="ghost" onClick={() => setNoteOpen(false)}>Skip</Button>
            <Button size="sm" variant="primary" type="submit" loading={busy} disabled={!note.trim()}>Send note</Button>
          </div>
        </form>
      )}
    </div>
  );
}
