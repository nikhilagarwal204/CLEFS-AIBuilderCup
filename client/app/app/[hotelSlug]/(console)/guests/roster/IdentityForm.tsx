"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The guest's own phone/email for this room, with the boxes the guest
   ticked. Posted to /guests/rooms/{room}/identity, never into the roster
   blob: the server hashes the contact, keeps a sealed copy only on a
   consented guest_direct point, and creates a profile ONLY with both
   "remember me" and data-transfer consent. Nothing here is pre-ticked.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, ApiError } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Field, Input, Select, useToast } from "../../../../../ui";
import { phonePlaceholder } from "../../../../../lib/phone";
import { CONSENT_LANG_OPTIONS, errText, gstApi, PURPOSE_LABEL, splitList } from "../lib";
import type { ConsentFlags, ConsentLang } from "../types";

const NO_CONSENT: ConsentFlags = {
  transfer_abroad: false, profile_memory: false, whatsapp_updates: false, marketing_whatsapp: false, marketing_email: false,
};

export default function IdentityForm({
  room, defaults, onSaved,
}: {
  room: string;
  defaults: { firstName: string; checkIn?: string; checkOut?: string };
  onSaved: () => void;
}) {
  const { slug, hotel } = useHotel();
  const toast = useToast();
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState(defaults.firstName);
  const [lang, setLang] = useState<ConsentLang>("en");
  const [consents, setConsents] = useState<ConsentFlags>(NO_CONSENT);
  const [allergies, setAllergies] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!phone.trim() && !email.trim()) {
      toast.error("Add a phone or an email");
      return;
    }
    if (!consents.transfer_abroad) {
      toast.error("Data-transfer consent missing", "Without the guest's data-transfer yes, their contact cannot be kept.");
      return;
    }
    setSaving(true);
    setResult(null);
    try {
      const r = await api.post<{ profile_id: number | null; contact: { phone: string | null; email: string | null } }>(
        gstApi(slug, `/rooms/${encodeURIComponent(room)}/identity`),
        {
          phone: phone.trim() || undefined,
          email: email.trim() || undefined,
          first_name: firstName.trim() || undefined,
          consents,
          consent_language: lang,
          preferences: allergies.trim() ? { tags: [], notes: null, health: { allergies: splitList(allergies), notes: null } } : undefined,
          check_in: defaults.checkIn || undefined,
          check_out: defaults.checkOut || undefined,
        },
      );
      const shown = [r.contact.phone, r.contact.email].filter(Boolean).join(" and ");
      setResult(`Saved ${shown || "the contact"}.${r.profile_id ? " The guest will be recognised on their next stay." : " No profile: the guest did not ask to be remembered."}`);
      setPhone(""); setEmail(""); setConsents(NO_CONSENT); setAllergies("");
      onSaved();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : undefined;
      toast.error("Could not save the contact", code === "consent_not_allowed"
        ? "These consents are not allowed here. The data-transfer tick is required." : errText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="gst-form-grid" onSubmit={save} style={{ marginTop: 22 }} data-clarity-mask="true">
      <div className="gst-form-grid__wide">
        <div className="gst-section-title" style={{ marginTop: 0 }}>Guest&apos;s own contact</div>
        <p className="gst-note">Only with the guest&apos;s permission. Tick exactly what the guest agreed to; each box is a separate yes.</p>
      </div>
      <Field label="Phone" htmlFor="gst-id-phone">
        <Input id="gst-id-phone" inputMode="tel" autoComplete="off" placeholder={phonePlaceholder(hotel?.country)} value={phone} onChange={e => setPhone(e.target.value)} />
      </Field>
      <Field label="Email" htmlFor="gst-id-email">
        <Input id="gst-id-email" type="email" autoComplete="off" value={email} onChange={e => setEmail(e.target.value)} />
      </Field>
      <Field label="First name" htmlFor="gst-id-name">
        <Input id="gst-id-name" maxLength={80} value={firstName} onChange={e => setFirstName(e.target.value)} />
      </Field>
      <Field label="Language the guest read" htmlFor="gst-id-lang">
        <Select id="gst-id-lang" value={lang} onChange={e => setLang(e.target.value as ConsentLang)}
          options={CONSENT_LANG_OPTIONS} />
      </Field>
      <fieldset className="gst-checks gst-form-grid__wide" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="gst-small gst-muted" style={{ marginBottom: 6 }}>The guest agreed to</legend>
        {(Object.keys(NO_CONSENT) as (keyof ConsentFlags)[]).map(k => (
          <label key={k} className={`gst-check${k === "transfer_abroad" ? " gst-check--req" : ""}`}>
            <input type="checkbox" checked={consents[k]} onChange={() => setConsents(c => ({ ...c, [k]: !c[k] }))} />
            <span>{PURPOSE_LABEL[k]}</span>
          </label>
        ))}
      </fieldset>
      {consents.profile_memory && (
        <Field label="Allergies (optional)" htmlFor="gst-id-allergies" hint="Health data: kept in the profile and shown only to F&B and front desk" className="gst-form-grid__wide">
          <Input id="gst-id-allergies" value={allergies} onChange={e => setAllergies(e.target.value)} placeholder="nuts, shellfish" />
        </Field>
      )}
      <div className="cx-row gst-form-grid__wide" style={{ justifyContent: "space-between" }}>
        <span className="gst-small gst-muted" role="status">{result}</span>
        <Button type="submit" variant="secondary" loading={saving}>Save contact</Button>
      </div>
    </form>
  );
}
