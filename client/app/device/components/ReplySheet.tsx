"use client";

/* "Reply to guest" (05 spec: staff in their own language). The worker types
   or holds the microphone and speaks in their own language; the server has
   Gemini transcribe a voice note, translates the message into the guest's
   language and answers with what the guest will read, which this sheet
   shows so the worker knows what went out.

   Hold to record: press and hold (finger, mouse, or Space/Enter), release
   to send; 30 s at most, and a tap shorter than 0.7 s is not sent. The
   microphone is released after every note. */

import { useCallback, useEffect, useRef, useState } from "react";
import { LuMic, LuSend } from "react-icons/lu";
import { ApiError } from "../../lib/api";
import { Button, Textarea } from "../../ui";
import type { Card } from "../lib/deviceApi";
import { jobTitle } from "../lib/cardView";
import { languageName } from "../lib/i18n";
import { blobToBase64, MicError, VOICE_MAX_MS, VOICE_MIN_MS, VoiceRecorder } from "../lib/voiceNote";
import { guestLanguageOf, type ReplyInput, type ReplyResult } from "../lib/worker";
import { useStaffI18n } from "./StaffI18n";

type Phase = "idle" | "opening" | "recording" | "sending";

/** The reply route takes at most 1000 characters of text. */
const REPLY_MAX_CHARS = 1000;

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

export function ReplySheet({ card: c, send, onClose }: {
  card: Card;
  send: (c: Card, input: ReplyInput) => Promise<ReplyResult>;
  onClose: () => void;
}) {
  const { t, lang } = useStaffI18n();
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [secs, setSecs] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [sent, setSent] = useState<ReplyResult[]>([]);
  const [voiceOk] = useState(() => VoiceRecorder.supported());

  const rec = useRef<VoiceRecorder | null>(null);
  const holding = useRef(false);
  const tick = useRef<ReturnType<typeof setInterval> | null>(null);
  const cap = useRef<ReturnType<typeof setTimeout> | null>(null);

  const guestLang = guestLanguageOf(c);
  const guestLangName = languageName(guestLang, lang);
  const busy = phase === "sending";

  const stopTimers = () => {
    if (tick.current) clearInterval(tick.current);
    if (cap.current) clearTimeout(cap.current);
    tick.current = null;
    cap.current = null;
  };

  // Leaving the sheet mid-recording drops the note and turns the mic off.
  useEffect(() => () => {
    stopTimers();
    rec.current?.release();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || phase !== "idle") return;
      e.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, onClose]);

  const deliver = useCallback(async (input: ReplyInput) => {
    setPhase("sending");
    setError(null);
    setHint(null);
    try {
      const res = await send(c, input);
      setSent(prev => [...prev, res]);
      if ("text" in input) setText("");
    } catch (err) {
      setError(t("reply.failed", { error: errorText(err) }));
    } finally {
      setPhase("idle");
    }
  }, [c, send, t]);

  const finish = useCallback(async () => {
    holding.current = false;
    const r = rec.current;
    if (!r || !r.isRecording) return; // still opening: begin() sees the release and stops there
    stopTimers();
    const note = await r.stop();
    if (!note || note.ms < VOICE_MIN_MS || note.blob.size === 0) {
      setPhase("idle");
      setHint(t("reply.tooShort"));
      return;
    }
    try {
      await deliver({ audio_b64: await blobToBase64(note.blob), mime: note.mime });
    } catch (err) {
      setPhase("idle");
      setError(t("reply.failed", { error: errorText(err) }));
    }
  }, [deliver, t]);

  const begin = useCallback(async () => {
    if (holding.current || phase !== "idle") return;
    holding.current = true;
    setError(null);
    setHint(null);
    setPhase("opening");
    const r = rec.current ?? (rec.current = new VoiceRecorder());
    try {
      await r.open();
    } catch (err) {
      holding.current = false;
      setPhase("idle");
      setError(err instanceof MicError && err.problem === "blocked" ? t("reply.micBlocked") : t("reply.noVoice"));
      return;
    }
    if (!holding.current) {
      // Let go while the permission prompt was up: nothing recorded.
      r.release();
      setPhase("idle");
      setHint(t("reply.hold"));
      return;
    }
    try {
      r.start();
    } catch {
      holding.current = false;
      r.release();
      setPhase("idle");
      setError(t("reply.noVoice"));
      return;
    }
    const t0 = Date.now();
    setSecs(0);
    setPhase("recording");
    tick.current = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 250);
    cap.current = setTimeout(() => { void finish(); }, VOICE_MAX_MS);
  }, [phase, t, finish]);

  const recording = phase === "recording";

  return (
    <div className="svc-dv-scrim" role="presentation" onClick={e => { if (phase === "idle" && e.target === e.currentTarget) onClose(); }}>
      <div className="svc-dv-sheet" role="dialog" aria-modal="true" aria-labelledby="svc-dv-reply-title">
        <div className="svc-dv-sheet__head">
          <h2 id="svc-dv-reply-title" className="svc-dv-sheet__title">
            {t("reply.title")}
            <span className="svc-dv-sheet__sub" dir="auto">
              {c.room ? t("job.room", { room: c.room }) : t("job.lobby")} · {jobTitle(c)}
            </span>
          </h2>
          <button type="button" className="svc-dv-iconbtn" onClick={onClose} disabled={phase !== "idle"}>{t("action.close")}</button>
        </div>

        {guestLangName && <p className="svc-dv-hint">{t("reply.guestReadsIn", { lang: guestLangName })}</p>}

        {sent.length > 0 && (
          <div className="svc-dv-section" aria-live="polite">
            {sent.map((r, i) => (
              <div key={i} className="svc-dv-replied">
                <h3>{t("reply.guestReads")}{languageName(r.language ?? guestLang, lang) ? ` · ${languageName(r.language ?? guestLang, lang)}` : ""}</h3>
                <div className="svc-dv-bubble svc-dv-bubble--guest" dir="auto" lang={r.language ?? guestLang ?? undefined}>{r.text_for_guest}</div>
                {r.text_original && r.text_original !== r.text_for_guest && (
                  <>
                    <h3>{t("reply.youSaid")}</h3>
                    <div className="svc-dv-bubble" dir="auto">{r.text_original}</div>
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="svc-dv-section">
          <Textarea rows={3} value={text} dir="auto" maxLength={REPLY_MAX_CHARS} placeholder={t("reply.placeholder")}
            aria-label={t("reply.placeholder")} disabled={busy || recording} onChange={e => setText(e.target.value)} />
          <Button variant="primary" icon={<LuSend aria-hidden="true" />} loading={busy && !recording}
            disabled={!text.trim() || busy || recording} onClick={() => { void deliver({ text }); }}>
            {busy ? t("reply.sending") : t("action.send")}
          </Button>
        </div>

        {voiceOk && (
          <>
            <div className="svc-dv-or"><span>{t("reply.or")}</span></div>
            <button
              type="button"
              className={`svc-dv-mic${recording ? " svc-dv-mic--rec" : ""}`}
              disabled={busy}
              aria-pressed={recording}
              onPointerDown={e => {
                if (e.button !== 0) return;
                e.preventDefault();
                try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* old engines */ }
                void begin();
              }}
              onPointerUp={() => { void finish(); }}
              onPointerCancel={() => { void finish(); }}
              onLostPointerCapture={() => { if (holding.current) void finish(); }}
              onKeyDown={e => {
                if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); void begin(); }
              }}
              onKeyUp={e => {
                if (e.key === " " || e.key === "Enter") { e.preventDefault(); void finish(); }
              }}
              onContextMenu={e => e.preventDefault()}
            >
              <LuMic aria-hidden="true" />
              <span>{recording ? t("reply.recording", { secs }) : t("reply.hold")}</span>
            </button>
          </>
        )}
        {!voiceOk && <p className="svc-dv-hint">{t("reply.noVoice")}</p>}

        {(error || hint) && (
          <p className={error ? "svc-dv-error" : "svc-dv-hint"} role={error ? "alert" : "status"}>{error ?? hint}</p>
        )}
      </div>
    </div>
  );
}
