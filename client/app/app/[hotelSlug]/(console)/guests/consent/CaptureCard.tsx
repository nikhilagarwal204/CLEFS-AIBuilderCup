"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Registration-card capture (04 spec 3.4, Canon 19): the guest wrote their
   own phone or email on the card and ticked boxes. Staff copy exactly what
   the guest ticked (never pre-ticked here) and attach minimal evidence:
   - a crop of ONLY the card's contact-and-consent block (<= 300 KB), or
   - a staff attestation plus the guest's signature image.
   Never a passport, Emirates ID or the whole card: the server rejects an
   image with a machine-readable zone and Clefs must not store ID scans.
   The data-transfer tick is required for every capture (PDPL).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, ApiError, uploadFile } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Field, Input, Segmented, Select, useToast } from "../../../../../ui";
import { phonePlaceholder } from "../../../../../lib/phone";
import { CONSENT_LANG_OPTIONS, errText, gstApi, PURPOSE_LABEL } from "../lib";
import type { ConsentFlags, ConsentLang, ContactKind } from "../types";

const MAX_EVIDENCE = 300 * 1024;
const EVIDENCE_PURPOSE = "guests.contact_evidence";
const NO_CONSENT: ConsentFlags = {
  transfer_abroad: false, profile_memory: false, whatsapp_updates: false, marketing_whatsapp: false, marketing_email: false,
};

type EvidenceKind = "card_crop" | "staff_attestation";

const CODE_TEXT: Record<string, string> = {
  consent_not_allowed: "These consents are not allowed for this contact (the data-transfer tick is required).",
  evidence_required: "Attach the evidence first.",
  evidence_too_large: "The evidence image is larger than 300 KB. Crop it to the contact block only.",
  evidence_has_id: "That image looks like an ID document. Crop only the card's contact-and-consent block.",
  evidence_unverified: "The image could not be checked right now (AI checks are off). Use a staff attestation with the guest's signature.",
  evidence_type: "The evidence must be an image.",
};

export default function CaptureCard() {
  const { slug, hotel } = useHotel();
  const toast = useToast();
  const [kind, setKind] = useState<ContactKind>("phone");
  const [value, setValue] = useState("");
  const [room, setRoom] = useState("");
  const [firstName, setFirstName] = useState("");
  const [language, setLanguage] = useState<ConsentLang>("en");
  const [consents, setConsents] = useState<ConsentFlags>(NO_CONSENT);
  const [evKind, setEvKind] = useState<EvidenceKind>("card_crop");
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const tick = (k: keyof ConsentFlags) => setConsents(c => ({ ...c, [k]: !c[k] }));

  function pickFile(f: File | null) {
    if (f && f.size > MAX_EVIDENCE) {
      toast.error("Image too large", CODE_TEXT.evidence_too_large);
      return;
    }
    if (f && !f.type.startsWith("image/")) {
      toast.error("Not an image", CODE_TEXT.evidence_type);
      return;
    }
    setFile(f);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!consents.transfer_abroad) {
      toast.error("Data-transfer tick missing", "The guest must tick the data-transfer box before their contact can be kept.");
      return;
    }
    if (!file) {
      toast.error("Evidence missing", evKind === "card_crop" ? "Attach the cropped consent block." : "Attach the guest's signature image.");
      return;
    }
    setSaving(true);
    setDone(null);
    try {
      const ref = await uploadFile(slug, file, EVIDENCE_PURPOSE);
      const evidence = evKind === "card_crop" ? { kind: evKind, file_id: ref.id } : { kind: evKind, signature_file_id: ref.id };
      const r = await api.post<{ contact: { value: string | null }; profile_id: number | null }>(gstApi(slug, "/contacts"), {
        kind, value: value.trim(), room: room.trim() || undefined, consents, language, evidence,
        first_name: firstName.trim() || undefined,
      });
      setDone(`Saved ${r.contact.value ?? "the contact"}${r.profile_id ? " and linked the guest profile" : ""}.`);
      setValue(""); setFirstName(""); setConsents(NO_CONSENT); setFile(null);
    } catch (err) {
      const code = err instanceof ApiError ? err.code : undefined;
      toast.error("Could not save the contact", (code && CODE_TEXT[code]) || errText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Registration card capture"
      subtitle="The guest's own phone or email from the registration card, with exactly the boxes they ticked.">
      <form className="gst-form-grid" onSubmit={submit} data-clarity-mask="true">
        <Field label="Contact" htmlFor="gst-cap-value" required>
          <div className="gst-toolbar">
            <Segmented<ContactKind> ariaLabel="Contact type" size="sm" value={kind} onChange={setKind}
              options={[{ value: "phone", label: "Phone" }, { value: "email", label: "Email" }]} />
            <div className="gst-grow">
              <Input id="gst-cap-value" required value={value} autoComplete="off" inputMode={kind === "phone" ? "tel" : "email"}
                placeholder={kind === "phone" ? phonePlaceholder(hotel?.country) : "guest@example.com"} onChange={e => setValue(e.target.value)} />
            </div>
          </div>
        </Field>
        <Field label="Room" htmlFor="gst-cap-room" hint="Links the contact to this stay">
          <Input id="gst-cap-room" value={room} maxLength={16} onChange={e => setRoom(e.target.value)} />
        </Field>
        <Field label="First name (optional)" htmlFor="gst-cap-name">
          <Input id="gst-cap-name" value={firstName} maxLength={80} onChange={e => setFirstName(e.target.value)} />
        </Field>
        <Field label="Card language" htmlFor="gst-cap-lang" hint="Records the wording version the guest read">
          <Select id="gst-cap-lang" value={language} onChange={e => setLanguage(e.target.value as ConsentLang)}
            options={CONSENT_LANG_OPTIONS} />
        </Field>

        <fieldset className="gst-checks gst-form-grid__wide" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="gst-section-title" style={{ marginTop: 0 }}>Boxes the guest ticked</legend>
          {(Object.keys(NO_CONSENT) as (keyof ConsentFlags)[]).map(k => (
            <label key={k} className={`gst-check${k === "transfer_abroad" ? " gst-check--req" : ""}`}>
              <input type="checkbox" checked={consents[k]} onChange={() => tick(k)} />
              <span>{PURPOSE_LABEL[k]}</span>
            </label>
          ))}
        </fieldset>

        <Field label="Evidence" htmlFor="gst-cap-file" required className="gst-form-grid__wide"
          hint={evKind === "card_crop"
            ? "A photo of ONLY the contact-and-consent block, 300 KB at most. Never the ID or passport section."
            : "Your attestation is recorded with your name; attach the guest's signature image (300 KB at most)."}>
          <div className="cx-stack" style={{ gap: 8 }}>
            <Segmented<EvidenceKind> ariaLabel="Evidence type" size="sm" value={evKind} onChange={v => { setEvKind(v); setFile(null); }}
              options={[{ value: "card_crop", label: "Card crop" }, { value: "staff_attestation", label: "Staff attestation" }]} />
            <input id="gst-cap-file" type="file" accept="image/jpeg,image/png,image/webp"
              onChange={e => pickFile(e.target.files?.[0] ?? null)} />
            {file && <span className="gst-small gst-muted">{file.name} · {Math.ceil(file.size / 1024)} KB</span>}
          </div>
        </Field>

        <div className="cx-row gst-form-grid__wide" style={{ justifyContent: "space-between" }}>
          <span className="gst-small gst-muted" role="status">{done}</span>
          <Button type="submit" loading={saving}>Save contact</Button>
        </div>
      </form>
    </Card>
  );
}
