"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Log a privacy request received by email, WhatsApp, phone or at the desk.
   At least one identifier is required (the server answers 400
   identifier_required otherwise); we check it client side too so the
   button explains itself instead of failing.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { DSR_TYPE_LABEL, errText, trustApi } from "../lib";
import type { Dsr, DsrChannel, DsrIdentifiers, DsrType } from "../types";

const CHANNELS: { value: DsrChannel; label: string }[] = [
  { value: "email", label: "Email" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "phone", label: "Phone" },
  { value: "in_person", label: "In person" },
  { value: "console", label: "Other (logged here)" },
];

const ID_FIELDS: { key: keyof DsrIdentifiers; label: string; type?: string }[] = [
  { key: "name", label: "Name" },
  { key: "email", label: "Email", type: "email" },
  { key: "phone", label: "Phone", type: "tel" },
  { key: "room", label: "Room" },
  { key: "reservation_ref", label: "Reservation reference" },
  { key: "stay_date", label: "A date of the stay", type: "date" },
];

const BLANK: Record<keyof DsrIdentifiers, string> = { name: "", email: "", phone: "", room: "", reservation_ref: "", stay_date: "" };

export default function NewDsrModal({ slug, open, onClose, onCreated }: {
  slug: string;
  open: boolean;
  onClose: () => void;
  onCreated: (d: Dsr) => void;
}) {
  const toast = useToast();
  const [type, setType] = useState<DsrType>("access");
  const [channel, setChannel] = useState<DsrChannel>("email");
  const [ids, setIds] = useState(BLANK);
  const [details, setDetails] = useState("");
  const [language, setLanguage] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const hasId = Object.values(ids).some(v => v.trim());

  function reset() {
    setType("access"); setChannel("email"); setIds(BLANK); setDetails(""); setLanguage(""); setErr(null);
  }

  async function submit() {
    if (!hasId) { setErr("Add at least one way to identify the person."); return; }
    setBusy(true); setErr(null);
    const identifiers: DsrIdentifiers = {};
    for (const f of ID_FIELDS) if (ids[f.key].trim()) identifiers[f.key] = ids[f.key].trim();
    try {
      const d = await api.post<Dsr>(trustApi(slug, "/dsr"), {
        type, channel, identifiers,
        details: details.trim() || undefined,
        language: language.trim() || undefined,
      });
      toast.success("Request logged", `Due ${d.due_at.slice(0, 10)}. Verify the person before acting on it.`);
      reset();
      onCreated(d);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose(); }}
      title="Log a privacy request"
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} loading={busy} disabled={!hasId}>Log request</Button>
        </>
      }
    >
      <div className="cx-stack" style={{ gap: 12 }}>
        <div className="trs-filters">
          <Field label="Request" htmlFor="dsr-type">
            <Select id="dsr-type" value={type} onChange={e => setType(e.target.value as DsrType)}
                    options={(Object.keys(DSR_TYPE_LABEL) as DsrType[]).map(k => ({ value: k, label: DSR_TYPE_LABEL[k] }))} />
          </Field>
          <Field label="Received by" htmlFor="dsr-channel">
            <Select id="dsr-channel" value={channel} onChange={e => setChannel(e.target.value as DsrChannel)} options={CHANNELS} />
          </Field>
          <Field label="Language" htmlFor="dsr-lang" hint="For the reply, e.g. en or ar">
            <Input id="dsr-lang" value={language} maxLength={8} onChange={e => setLanguage(e.target.value)} />
          </Field>
        </div>
        <div className="trs-section-title" style={{ margin: "6px 0 0" }}>Who is asking (at least one)</div>
        <div className="trs-filters">
          {ID_FIELDS.map(f => (
            <Field key={f.key} label={f.label} htmlFor={`dsr-${f.key}`}>
              <Input id={`dsr-${f.key}`} type={f.type ?? "text"} value={ids[f.key]}
                     onChange={e => setIds(prev => ({ ...prev, [f.key]: e.target.value }))} />
            </Field>
          ))}
        </div>
        <Field label="What they asked for" htmlFor="dsr-details" hint="Their words, or a short summary. Stored sealed.">
          <Textarea id="dsr-details" rows={3} value={details} maxLength={4000} onChange={e => setDetails(e.target.value)} />
        </Field>
        {err && <div role="alert" style={{ color: "var(--cx-bad)" }}>{err}</div>}
      </div>
    </Modal>
  );
}
