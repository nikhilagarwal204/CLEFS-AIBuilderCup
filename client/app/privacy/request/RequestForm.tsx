"use client";

/**
 * Public privacy (DSR) request form: POST /public/privacy/requests (trust
 * module). Anonymous by design: no session, no cookies needed, and the server
 * answers 202 whether or not the person is known, so the form never reveals
 * who stayed at a hotel. The `website` field is the server's honeypot and is
 * kept visually hidden and out of the tab order.
 *
 * Identifier rule mirrors the server (400 identifier_required): an email, a
 * phone, or a room number together with a stay date.
 */

import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../../lib/api";
import { Button, UiStyles } from "../../ui/core";
import { Field, Input, Select, Textarea } from "../../ui/forms";
import { DSR_TYPES, FORM_COPY, fill, type DsrType, type PrivacyLang } from "../_copy";

// ─── Types ───────────────────────────────────────────────────────────────────

type FormContext = { hotel: { slug: string; name: string; guest_languages?: string[] }; types: string[]; due_days: number };
type Status = "idle" | "sending" | "sent";

const DEFAULT_DUE_DAYS = 30;

// ─── Component ───────────────────────────────────────────────────────────────

export default function RequestForm({ hotelSlug, lang }: { hotelSlug: string | null; lang: PrivacyLang }) {
  const t = FORM_COPY[lang];
  const [code, setCode] = useState(hotelSlug ?? "");
  const [ctx, setCtx] = useState<FormContext | null>(null);
  const [hotelError, setHotelError] = useState<string | null>(null);
  const [type, setType] = useState<DsrType>("access");
  const [f, setF] = useState({ name: "", email: "", phone: "", room: "", stay_date: "", reservation_ref: "", details: "", website: "" });
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);

  const dueDays = ctx?.due_days ?? DEFAULT_DUE_DAYS;
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));

  // Resolve the hotel from the link. A lookup failure other than 404 (API
  // down) leaves the form usable: the POST is the real check.
  useEffect(() => {
    if (!hotelSlug) return;
    let live = true;
    api.get<FormContext>("/public/privacy/form", { hotel: hotelSlug }, { silent401: true })
      .then((c) => { if (live) setCtx(c); })
      .catch((e: unknown) => {
        if (live && e instanceof ApiError && e.status === 404) setHotelError(t.hotelUnknown);
      });
    return () => { live = false; };
  }, [hotelSlug, t.hotelUnknown]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const hotel = (ctx?.hotel.slug ?? code).trim().toLowerCase();
    // Empty is "tell us the code", not "we looked and found nothing" (QA PUB-7).
    if (!hotel) { setHotelError(t.hotelRequired); return; }
    const hasId = f.email.trim() || f.phone.trim() || (f.room.trim() && f.stay_date);
    if (!hasId) { setError(t.errIdentifier); return; }
    setStatus("sending");
    try {
      await api.post("/public/privacy/requests", {
        hotel, type, language: lang,
        name: f.name.trim() || null, email: f.email.trim() || null, phone: f.phone.trim() || null,
        room: f.room.trim() || null, stay_date: f.stay_date || null,
        reservation_ref: f.reservation_ref.trim() || null, details: f.details.trim() || null,
        website: f.website || null,
      }, { silent401: true });
      setStatus("sent");
    } catch (err) {
      setStatus("idle");
      const code = err instanceof ApiError ? err.code : undefined;
      const st = err instanceof ApiError ? err.status : 0;
      if (st === 404 || code === "hotel_not_found") setHotelError(t.hotelUnknown);
      else if (code === "identifier_required") setError(t.errIdentifier);
      else if (st === 429) setError(t.errRate);
      else setError(t.errGeneric);
    }
  }

  if (status === "sent") {
    return (
      <div className="mk-form mk-form--done" role="status">
        <UiStyles />
        <h2 className="mk-h2">{t.sentTitle}</h2>
        <p>{fill(t.sent, { days: dueDays })}</p>
      </div>
    );
  }

  return (
    <form className="mk-form" onSubmit={onSubmit} noValidate>
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: FORM_STYLES }} />
      {ctx ? (
        <p className="mk-form__hotel"><span>{t.hotel}</span> <strong>{ctx.hotel.name}</strong></p>
      ) : (
        <Field label={t.hotelCode} hint={t.hotelCodeHelp} error={hotelError} required htmlFor="dsr-hotel">
          <Input id="dsr-hotel" value={code} onChange={(e) => { setCode(e.target.value); setHotelError(null); }}
                 autoComplete="off" dir="ltr" invalid={Boolean(hotelError)} />
        </Field>
      )}
      <Field label={t.type} required htmlFor="dsr-type">
        <Select id="dsr-type" value={type} onChange={(e) => setType(e.target.value as DsrType)}>
          {DSR_TYPES.map((k) => <option key={k} value={k}>{t.types[k]}</option>)}
        </Select>
      </Field>
      <fieldset className="mk-form__group">
        <legend>{t.who}</legend>
        <p className="mk-form__help">{t.whoHelp}</p>
        <div className="mk-form__grid">
          <Field label={t.name}><Input value={f.name} onChange={set("name")} autoComplete="name" /></Field>
          <Field label={t.email}><Input type="email" value={f.email} onChange={set("email")} autoComplete="email" dir="ltr" /></Field>
          <Field label={t.phone}><Input type="tel" value={f.phone} onChange={set("phone")} autoComplete="tel" dir="ltr" /></Field>
          <Field label={t.room}><Input value={f.room} onChange={set("room")} inputMode="numeric" dir="ltr" /></Field>
          <Field label={t.stayDate}><Input type="date" value={f.stay_date} onChange={set("stay_date")} dir="ltr" /></Field>
          <Field label={t.reservation}><Input value={f.reservation_ref} onChange={set("reservation_ref")} dir="ltr" /></Field>
        </div>
      </fieldset>
      <Field label={t.details}><Textarea rows={4} value={f.details} onChange={set("details")} maxLength={4000} /></Field>
      <div className="mk-form__hp" aria-hidden="true">
        <label>Website<input tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} /></label>
      </div>
      {error && <p className="mk-form__error" role="alert">{error}</p>}
      <Button type="submit" variant="primary" loading={status === "sending"}>
        {status === "sending" ? t.sending : t.submit}
      </Button>
    </form>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────────────

const FORM_STYLES = `
  .mk-form { display: grid; gap: 18px; max-width: 640px; margin: 28px 0 8px; padding: 24px;
             border: 1px solid var(--clefs-line); border-radius: 14px; background: var(--clefs-surface); }
  .mk-form--done { border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
  .mk-form__hotel { margin: 0; color: var(--clefs-ink-muted); }
  .mk-form__hotel strong { color: var(--clefs-ink); font-weight: 600; }
  .mk-form__group { border: 0; padding: 0; margin: 0; display: grid; gap: 10px; }
  .mk-form__group legend { font-family: var(--clefs-serif); font-size: 1.1rem; color: var(--clefs-ink); padding: 0; margin-bottom: 4px; }
  .mk-form__help { margin: 0; font-size: 0.9rem; line-height: 1.6; color: var(--clefs-ink-muted); }
  .mk-form__grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .mk-form__error { margin: 0; color: var(--cx-bad, #E5484D); font-size: 0.92rem; }
  .mk-form__hp { position: absolute; left: -10000px; width: 1px; height: 1px; overflow: hidden; }
  @media (max-width: 640px) { .mk-form { padding: 18px 16px; } }
`;
