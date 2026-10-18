"use client";

/* Add or edit a venue. The short code is the last part of the venue's QR
   link, so it is fixed once created (renaming it would break printed QR
   codes); the save is an upsert on (hotel, code) server-side. */

import { useState } from "react";
import { api, hotelPath } from "../../../../../lib/api";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { errText } from "../lib";
import type { Venue } from "../types";

const TYPES = ["public", "restaurant", "bar", "pool", "spa", "gym", "lobby", "beach", "meeting", "other"];
const NOTE_MAX = 4000;

function slugify(s: string): string {
  return s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}

export function VenueModal({ slug, venue, existing, onClose, onSaved }: {
  slug: string;
  venue: Venue | null;
  existing: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(venue?.name ?? "");
  const [code, setCode] = useState(venue?.slug ?? "");
  const [codeTouched, setCodeTouched] = useState(false);
  const [type, setType] = useState(venue?.type ?? "public");
  const [note, setNote] = useState(venue?.custom_instructions ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const effectiveCode = venue ? venue.slug : codeTouched ? code : slugify(name);

  async function save() {
    // A numeric code would be read as a room number by the guest page.
    if (!name.trim() || !effectiveCode || /^\d+$/.test(effectiveCode)) {
      setErr("Give the venue a name and a code with at least one letter (the code is the end of its QR link).");
      return;
    }
    if (!venue && existing.includes(effectiveCode)) { setErr("Another venue already uses that code."); return; }
    setBusy(true);
    setErr(null);
    try {
      await api.post(hotelPath(slug, "/locations"), {
        slug: effectiveCode, name: name.trim(), type, custom_instructions: note.trim() || null,
      });
      toast.success(venue ? "Venue saved" : "Venue added");
      onSaved();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={venue ? `Edit ${venue.name}` : "Add venue"} footer={
      <div className="cx-row">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button loading={busy} onClick={() => void save()}>Save</Button>
      </div>
    }>
      <div className="cx-stack">
        <div className="cc-fields">
          <Field label="Name" required><Input value={name} onChange={e => setName(e.target.value)} placeholder="Pool bar" /></Field>
          <Field label="Type"><Select value={type} onChange={e => setType(e.target.value)} options={TYPES.map(t => ({ value: t, label: t }))} /></Field>
          <Field label="Code" className="cc-fields__wide" hint={venue ? "Fixed: printed QR codes use it." : "The end of the QR link. Letters, digits and dashes."}>
            <Input value={effectiveCode} readOnly={!!venue} className="cc-mono"
              onChange={e => { setCodeTouched(true); setCode(slugify(e.target.value)); }} />
          </Field>
        </div>
        <Field label="Venue note" hint="What the concierge should know only here: hours, last orders, towels, dress code. No prices unless they are on the menu.">
          <Textarea rows={6} value={note} onChange={e => setNote(e.target.value.slice(0, NOTE_MAX))} />
        </Field>
        {err && <div className="cc-callout" role="alert">{err}</div>}
      </div>
    </Modal>
  );
}
