"use client";

/**
 * Lead unlock (02 spec 3.11): name, hotel, an email or a WhatsApp number and
 * the contact consent open the full report. Separate ticks, one purpose
 * each (PDPL): contact and the transfer abroad of the contact details (the
 * database is in Singapore, the founder alert goes through a US email
 * provider) are required to unlock; the Leakage Index is optional and
 * unticked by default. The wording is the server's CONSENT_TEXTS
 * (public_audit.py, version 2026-09-29), which is stored with the grant, so
 * what the visitor ticks is exactly what is recorded.
 *
 * The optional hotel details (area, website, stars) only steer the AI
 * snapshot's subject; the city defaults to Dubai on the server.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { Button } from "../../../ui/core";
import { Field, Input, Select } from "../../../ui/forms";
import { errorDetail, errorShape, postLead } from "../../_lib";
import {
  CONSENT_CONTACT, CONSENT_INDEX, CONSENT_LEAD_TRANSFER, FAILURE_TEXT, failureOf, figureOk, rangeText,
} from "../../_view";
import type { AuditView, LeadBody, Teaser } from "../../_types";

export default function LeadForm({
  token, teaser, onUnlocked,
}: { token: string; teaser: Teaser; onUnlocked: (a: AuditView) => void }) {
  const [name, setName] = useState("");
  const [hotel, setHotel] = useState("");
  const [email, setEmail] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [city, setCity] = useState("");
  const [area, setArea] = useState("");
  const [website, setWebsite] = useState("");
  const [star, setStar] = useState("");
  const [contact, setContact] = useState(false);
  const [transfer, setTransfer] = useState(false);
  const [index, setIndex] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    if (!name.trim()) { setMessage("Add your name."); return; }
    if (hotel.trim().length < 2) { setMessage("Add your hotel's name."); return; }
    if (!email.trim() && !whatsapp.trim()) { setMessage("Add an email or a WhatsApp number."); return; }
    if (whatsapp.trim() && !whatsapp.trim().startsWith("+")) {
      setMessage("Enter the WhatsApp number with its country code, for example +971 50 123 4567.");
      return;
    }
    if (!contact) { setMessage("Tick the box that lets Clefs contact you about the audit."); return; }
    if (!transfer) {
      setMessage("Tick the box that lets Clefs' sub-processors outside the UAE handle your contact details.");
      return;
    }
    const body: LeadBody = {
      name: name.trim(),
      hotel_name: hotel.trim(),
      email: email.trim() || null,
      whatsapp: whatsapp.trim() || null,
      consent_contact: true,
      consent_transfer_abroad: true,
      consent_index: index,
      city: city.trim() || null,
      area: area.trim() || null,
      website: website.trim() || null,
      star: star ? Number(star) : null,
    };
    setBusy(true);
    try {
      onUnlocked(await postLead(token, body));
    } catch (err) {
      const f = failureOf(errorShape(err), true);
      // A 422 invalid_input names the field; show the server's own sentence.
      setMessage(f === "invalid" ? errorDetail(err) ?? FAILURE_TEXT.invalid : FAILURE_TEXT[f]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="oa-card" onSubmit={onSubmit} noValidate aria-busy={busy}>
      <h2 className="oa-h">See every finding</h2>
      {/* The figure is named only for a teaser built under the public classes (figureOk). */}
      <p className="oa-sub">
        The full report lists each booking behind {teaser.findings > 0 && figureOk(teaser) ? rangeText(teaser) : "the checks"}, with the
        stay, the calculation, the dispute deadline and what the audit could not see, plus a short AI snapshot of
        your hotel.
      </p>
      <div className="oa-grid">
        <Field label="Your name" required><Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={120} /></Field>
        <Field label="Hotel name" required><Input value={hotel} onChange={(e) => setHotel(e.target.value)} autoComplete="organization" maxLength={160} /></Field>
        <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" maxLength={200} /></Field>
        <Field label="WhatsApp" hint="With country code"><Input type="tel" value={whatsapp} onChange={(e) => setWhatsapp(e.target.value)} autoComplete="tel" placeholder="+971" maxLength={40} /></Field>
      </div>
      <details>
        <summary className="oa-sub">Hotel details for the AI snapshot (optional)</summary>
        <div className="oa-grid" style={{ marginTop: 12 }}>
          <Field label="City"><Input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Dubai" maxLength={80} /></Field>
          <Field label="Area"><Input value={area} onChange={(e) => setArea(e.target.value)} placeholder="Deira" maxLength={80} /></Field>
          <Field label="Website"><Input type="url" value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://" maxLength={200} /></Field>
          <Field label="Stars">
            <Select value={star} onChange={(e) => setStar(e.target.value)}>
              <option value="">Not set</option>
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={String(n)}>{n}</option>)}
            </Select>
          </Field>
        </div>
      </details>
      <fieldset className="oa-ticks">
        <legend className="oa-sr">Consent</legend>
        <label><input type="checkbox" checked={contact} onChange={(e) => setContact(e.target.checked)} /> <span>{CONSENT_CONTACT} (required)</span></label>
        <label>
          <input type="checkbox" checked={transfer} onChange={(e) => setTransfer(e.target.checked)} />{" "}
          <span>
            {CONSENT_LEAD_TRANSFER} (<Link href="/subprocessors" target="_blank">sub-processors</Link>) (required)
          </span>
        </label>
        <label><input type="checkbox" checked={index} onChange={(e) => setIndex(e.target.checked)} /> <span>{CONSENT_INDEX} (optional)</span></label>
      </fieldset>
      {message && <p className="oa-msg" role="alert">{message}</p>}
      <Button type="submit" variant="primary" loading={busy} disabled={busy}>Open the full report</Button>
    </form>
  );
}
