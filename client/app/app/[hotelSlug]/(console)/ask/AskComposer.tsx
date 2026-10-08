"use client";

/* ═══════════════════════════════════════════════════════════════════════
   AskComposer (02 spec 7): the question box. Enter sends, Shift+Enter
   breaks the line (IME composition never sends). The 500-character limit
   is the server's guardrail; the counter warns before it, and a longer
   paste stays editable instead of being cut. Starter questions come from
   the viewer's own catalog (lib.starterQuestions), so every one is
   answerable for their role.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { LuArrowUp } from "react-icons/lu";
import { Button, Card, Textarea } from "../../../../ui";
import { MAX_QUESTION } from "./lib";

export default function AskComposer({ onAsk, busy, disabled, disabledNote, starters }: {
  onAsk: (question: string) => void;
  /** A question is in flight: typing stays open, sending waits. */
  busy: boolean;
  disabled?: boolean;
  disabledNote?: string | null;
  starters: readonly string[];
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const trimmed = text.trim();
  const over = trimmed.length > MAX_QUESTION;
  const canSend = !!trimmed && !over && !busy && !disabled;

  // Grow with the text up to the CSS max-height, then scroll.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  /** A starter chip sends its own text and leaves a half-typed draft alone. */
  function send(q = trimmed, fromDraft = true) {
    if (!q || q.length > MAX_QUESTION || busy || disabled) return;
    onAsk(q);
    if (fromDraft) setText("");
    ref.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  }

  const left = MAX_QUESTION - trimmed.length;

  return (
    <Card className="ask-composer" gold>
      <form className="ask-composer__box" onSubmit={e => { e.preventDefault(); send(); }}>
        <Textarea
          ref={ref}
          rows={1}
          aria-label="Ask a question about your hotel's figures"
          placeholder="Ask about revenue, occupancy, requests, reviews…"
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
          invalid={over}
          maxLength={2000}
        />
        <Button type="submit" variant="primary" icon={<LuArrowUp />} loading={busy} disabled={!canSend}
                aria-label="Ask">
          Ask
        </Button>
      </form>
      <div className="ask-composer__foot">
        <span>{disabledNote || "Answers come only from your hotel's own figures, and every number is checked before you see it."}</span>
        {trimmed.length > MAX_QUESTION * 0.8 && (
          <span className={over ? "ask-composer__count--over" : undefined} aria-live="polite">
            {over ? `${-left} characters over the limit` : `${left} characters left`}
          </span>
        )}
      </div>
      {starters.length > 0 && (
        <div className="ask-chips" aria-label="Try one of these">
          {starters.map(s => (
            <button key={s} type="button" className="ask-chip" disabled={busy || disabled} onClick={() => send(s, false)}>{s}</button>
          ))}
        </div>
      )}
    </Card>
  );
}
