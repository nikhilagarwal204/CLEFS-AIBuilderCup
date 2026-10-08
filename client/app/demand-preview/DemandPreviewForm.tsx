"use client";

/**
 * Demand preview request form (03 section 6: "/demand-preview is a lead form").
 *
 * It is a request for a person to prepare a preview, not a self-serve tool:
 * Canon 23 allows exactly two automated lead magnets (the OTA audit and the
 * Google check). The founder builds the 30-day view by hand from Demand Radar.
 *
 * ENDPOINT: POST /public/leads/demand-preview is not built yet (no lane owns a
 * generic lead intake in wave 1). Until it exists the form degrades honestly:
 * a 404/405/501/503 or a network failure shows "book a call" with the demo
 * link instead of pretending the request was sent. Consents follow Canon 16 and
 * 04 section 2.4: each tick names Clefs and the channel, and none is pre-ticked.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { api, ApiError } from "../lib/api";
import { BRAND } from "../lib/seo";
import { Button, UiStyles } from "../ui/core";
import { Field, Input } from "../ui/forms";

// ─── Types ───────────────────────────────────────────────────────────────────

type Status = "idle" | "sending" | "sent" | "unavailable";

const EMPTY = {
  hotel_name: "", city: "", area: "", rooms: "", name: "", role: "", email: "", whatsapp: "", website: "",
};

const LEAD_PATH = "/public/leads/demand-preview";

// ─── Component ───────────────────────────────────────────────────────────────

export default function DemandPreviewForm() {
  const [f, setF] = useState(EMPTY);
  const [consent, setConsent] = useState({ processing: false, transfer: false, email: false, whatsapp: false });
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));
  const tick = (k: keyof typeof consent) => (e: { target: { checked: boolean } }) => setConsent((s) => ({ ...s, [k]: e.target.checked }));

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!f.hotel_name.trim() || !f.city.trim()) return setError("Add your hotel's name and city.");
    if (!f.email.trim() && !f.whatsapp.trim()) return setError("Add an email or a WhatsApp number so we can send the preview.");
    if (!consent.processing || !consent.transfer) return setError("Both processing ticks are needed to prepare the preview.");
    // The preview is delivered by message, so at least one filled-in channel
    // must carry its own contact consent.
    if (!((f.email.trim() && consent.email) || (f.whatsapp.trim() && consent.whatsapp))) {
      return setError("Tick how Clefs may contact you, on a channel you filled in.");
    }
    setStatus("sending");
    try {
      await api.post(LEAD_PATH, {
        hotel_name: f.hotel_name.trim(), city: f.city.trim(), area: f.area.trim() || null,
        rooms: f.rooms ? Number(f.rooms) : null, name: f.name.trim() || null, role: f.role.trim() || null,
        email: f.email.trim() || null, whatsapp: f.whatsapp.trim() || null,
        consent_processing: true, consent_transfer_abroad: true,
        consent_contact_email: consent.email, consent_contact_whatsapp: consent.whatsapp,
        website: f.website || null,
      }, { silent401: true });
      setStatus("sent");
    } catch (err) {
      const st = err instanceof ApiError ? err.status : 0;
      if (st === 0 || st === 404 || st === 405 || st === 501 || st === 503) { setStatus("unavailable"); return; }
      setStatus("idle");
      setError(st === 429 ? "You have reached today's limit, try again tomorrow." : "Something went wrong. Please try again.");
    }
  }

  if (status === "sent" || status === "unavailable") {
    return (
      <div className="mk-lead mk-lead--done" role="status">
        <UiStyles />
        <style dangerouslySetInnerHTML={{ __html: LEAD_STYLES }} />
        {status === "sent" ? (
          <>
            <h2 className="mk-h2">Request received</h2>
            <p>We will prepare the next 30 days around {f.hotel_name.trim() || "your hotel"} and send it on the channel you ticked, usually within two working days.</p>
          </>
        ) : (
          <>
            <h2 className="mk-h2">Online requests are not open yet</h2>
            <p>Nothing was sent. Book a short call instead and we will walk through the demand around your hotel live.</p>
            <a className="cb-btn cb-btn--gold" href={BRAND.demoUrl} target="_blank" rel="noopener noreferrer">Book a call</a>
          </>
        )}
      </div>
    );
  }

  return (
    <form className="mk-lead" onSubmit={onSubmit} noValidate>
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: LEAD_STYLES }} />
      <div className="mk-lead__grid">
        <Field label="Hotel name" required><Input value={f.hotel_name} onChange={set("hotel_name")} autoComplete="organization" /></Field>
        <Field label="City" required><Input value={f.city} onChange={set("city")} placeholder="Dubai" /></Field>
        <Field label="Area" hint="For example Business Bay or Yas Island"><Input value={f.area} onChange={set("area")} /></Field>
        <Field label="Rooms"><Input type="number" min={1} max={5000} value={f.rooms} onChange={set("rooms")} inputMode="numeric" /></Field>
        <Field label="Your name"><Input value={f.name} onChange={set("name")} autoComplete="name" /></Field>
        <Field label="Your role"><Input value={f.role} onChange={set("role")} placeholder="Owner, GM, revenue" /></Field>
        <Field label="Email"><Input type="email" value={f.email} onChange={set("email")} autoComplete="email" /></Field>
        <Field label="WhatsApp"><Input type="tel" value={f.whatsapp} onChange={set("whatsapp")} autoComplete="tel" placeholder="+971" /></Field>
      </div>
      <fieldset className="mk-lead__ticks">
        <legend>Consent</legend>
        <label><input type="checkbox" checked={consent.processing} onChange={tick("processing")} /> Clefs may process these details to prepare my demand preview. (required)</label>
        <label>
          <input type="checkbox" checked={consent.transfer} onChange={tick("transfer")} /> My request may be processed outside the UAE by
          Clefs&apos; <Link href="/subprocessors" target="_blank">sub-processors</Link>. (required)
        </label>
        <label><input type="checkbox" checked={consent.email} onChange={tick("email")} /> Clefs may contact me about this preview by email.</label>
        <label><input type="checkbox" checked={consent.whatsapp} onChange={tick("whatsapp")} /> Clefs may contact me about this preview on WhatsApp.</label>
      </fieldset>
      <div className="mk-lead__hp" aria-hidden="true">
        <label>Website<input tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} /></label>
      </div>
      {error && <p className="mk-lead__error" role="alert">{error}</p>}
      <Button type="submit" variant="primary" loading={status === "sending"}>Request my demand preview</Button>
    </form>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────────────

const LEAD_STYLES = `
  .mk-lead { display: grid; gap: 18px; max-width: 720px; padding: 24px; margin: 8px 0;
             border: 1px solid var(--clefs-line); border-radius: 14px; background: var(--clefs-surface); }
  .mk-lead--done { border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
  .mk-lead--done p { color: var(--clefs-ink-muted); line-height: 1.7; margin: 0 0 16px; }
  .mk-lead__grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .mk-lead__ticks { border: 0; padding: 0; margin: 0; display: grid; gap: 10px; }
  .mk-lead__ticks legend { font-family: var(--clefs-serif); font-size: 1.05rem; color: var(--clefs-ink); padding: 0; margin-bottom: 4px; }
  .mk-lead__ticks label { display: flex; gap: 10px; align-items: flex-start; font-size: 0.92rem;
                          line-height: 1.55; color: var(--clefs-ink-muted); cursor: pointer; }
  .mk-lead__ticks input { margin-top: 4px; accent-color: var(--clefs-gold-600); flex: none; }
  .mk-lead__error { margin: 0; color: var(--cx-bad, #E5484D); font-size: 0.92rem; }
  .mk-lead__hp { position: absolute; left: -10000px; width: 1px; height: 1px; overflow: hidden; }
  @media (max-width: 640px) { .mk-lead { padding: 18px 16px; } }
`;
