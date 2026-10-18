"use client";

/**
 * Lead unlock for a ready Google check (04 section 2.4 and 5.9): name plus an
 * email or WhatsApp, and two separate contact ticks that each name Clefs and
 * the channel. Neither tick is required to see the result; the server queues
 * founder follow-up only on a channel that was ticked.
 */

import { useState, type FormEvent } from "react";
import { Button } from "../../../ui/core";
import { Field, Input } from "../../../ui/forms";
import { failureMessage, unlock, type CheckResult } from "../../_lib";

export default function UnlockForm({ token, onUnlocked }: { token: string; onUnlocked: (r: CheckResult) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [byEmail, setByEmail] = useState(false);
  const [byWhatsapp, setByWhatsapp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    if (!name.trim()) { setMessage("Add your name."); return; }
    if (!email.trim() && !whatsapp.trim()) { setMessage("Add an email or a WhatsApp number."); return; }
    setBusy(true);
    try {
      const res = await unlock(token, {
        name: name.trim(),
        email: email.trim() || null,
        whatsapp: whatsapp.trim() || null,
        consent_contact_email: Boolean(email.trim()) && byEmail,
        consent_contact_whatsapp: Boolean(whatsapp.trim()) && byWhatsapp,
      });
      onUnlocked(res);
    } catch (err) {
      setMessage(failureMessage(err, true));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="gc-card" onSubmit={onSubmit} noValidate>
      <h2 className="mk-h2">See the parity table and your position</h2>
      <div className="gc-grid">
        <Field label="Your name" required><Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></Field>
        <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></Field>
        <Field label="WhatsApp"><Input type="tel" value={whatsapp} onChange={(e) => setWhatsapp(e.target.value)} autoComplete="tel" placeholder="+971" /></Field>
      </div>
      <fieldset className="gc-ticks">
        <legend className="gc-sr">Contact consent</legend>
        <label><input type="checkbox" checked={byEmail} onChange={(e) => setByEmail(e.target.checked)} /> Clefs may contact me about this check by email.</label>
        <label><input type="checkbox" checked={byWhatsapp} onChange={(e) => setByWhatsapp(e.target.checked)} /> Clefs may contact me on WhatsApp.</label>
      </fieldset>
      {message && <p className="gc-msg" role="alert">{message}</p>}
      <Button type="submit" variant="primary" loading={busy}>Show my result</Button>
    </form>
  );
}
