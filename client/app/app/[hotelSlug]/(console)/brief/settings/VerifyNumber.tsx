"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Add your own WhatsApp number (security-privacy-1). The number on an
   account is a global identity: magic links are minted by it and inbound
   WhatsApp messages map to the account by it. So nobody types a number
   in for anyone, and a member's own number is stored only once they send
   "CLEFS <code>" from that WhatsApp to the Clefs number (Meta names the
   sender). Step 1 asks for the number, step 2 shows the code and polls
   until the server has seen it.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState } from "react";
import { api, type ApiError } from "../../../../../lib/api";
import { Button, Field, Input, Modal } from "../../../../../ui";
import { brfApi } from "../lib";
import type { PhoneVerification } from "../types";

const ERR: Record<string, string> = {
  phone_taken: "Another account already uses that number.",
  phone_mismatch: "Your account already has a different number. Change it in the team settings.",
  already_verified: "That number is already on your account.",
  invalid_phone: "Enter the number with its country code, for example +971 50 123 4567.",
};
const POLL_MS = 4000;

export default function VerifyNumber({ slug, open, onClose, onVerified }: {
  slug: string;
  open: boolean;
  onClose: () => void;
  onVerified: () => void;
}) {
  const [phone, setPhone] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PhoneVerification | null>(null);
  const [state, setState] = useState<PhoneVerification["status"] | null>(null);
  // The parent's callback may change identity on every render; the poll
  // must not restart for that, so it reads the latest one through a ref.
  const verifiedCb = useRef(onVerified);
  useEffect(() => { verifiedCb.current = onVerified; }, [onVerified]);

  // The parent mounts this only while open, so each opening starts fresh.
  // Poll while a code is out; stop on any final state or when closed.
  useEffect(() => {
    if (!open || !pending) return undefined;
    let stopped = false;
    const tick = async () => {
      try {
        const s = await api.get<PhoneVerification>(brfApi(slug, "/wa/contacts/verify"));
        if (stopped) return;
        setState(s.status);
        if (s.status === "verified") { stopped = true; verifiedCb.current(); }
        if (s.status === "expired" || s.status === "cancelled") stopped = true;
      } catch { /* a missed poll is retried on the next tick */ }
    };
    const id = window.setInterval(() => { if (!stopped) void tick(); }, POLL_MS);
    return () => { stopped = true; window.clearInterval(id); };
  }, [open, pending, slug]);

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const out = await api.post<PhoneVerification>(brfApi(slug, "/wa/contacts/verify"), { phone: phone.trim() });
      setPending(out);
      setState(out.status);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ERR[ae.code ?? ""] ?? ae.message);
    } finally {
      setBusy(false);
    }
  };

  const over = state === "expired" || state === "cancelled";
  const footer = pending ? (
    <>
      <Button variant="ghost" onClick={onClose}>Close</Button>
      {over && <Button variant="primary" onClick={() => { setPending(null); setState(null); }}>Get a new code</Button>}
    </>
  ) : (
    <>
      <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
      <Button variant="primary" loading={busy} disabled={!phone.trim()} onClick={() => void start()}>Get a code</Button>
    </>
  );

  return (
    <Modal open={open} onClose={onClose} size="sm" title="Add your WhatsApp number" dismissible={!busy} footer={footer}>
      {!pending ? (
        <Field label="Your WhatsApp number" hint="With the country code, for example +971 50 123 4567." error={err ?? undefined}>
          <Input type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={e => setPhone(e.target.value)} autoFocus />
        </Field>
      ) : (
        <div className="brf-verify">
          <p>From WhatsApp on <b>{pending.phone_masked}</b>, send this message to {pending.send_to
            ? <b>{pending.send_to}</b> : "the Clefs WhatsApp number"}:</p>
          <p className="brf-verify__code" aria-label={`Message to send: ${pending.message ?? ""}`}>{pending.message}</p>
          {pending.wa_link && (
            <a className="cx-btn cx-btn--secondary cx-btn--sm" href={pending.wa_link} target="_blank" rel="noopener noreferrer">
              Open WhatsApp
            </a>
          )}
          <p className="brf-muted" role="status" aria-live="polite">
            {state === "verified" ? "Number confirmed. The brief will come to this WhatsApp."
              : over ? "This code is no longer valid. Get a new one."
              : "Waiting for your message. The code works for 30 minutes."}
          </p>
        </div>
      )}
    </Modal>
  );
}
