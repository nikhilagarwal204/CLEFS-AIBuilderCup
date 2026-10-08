"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Edit a profile (guests.manage). Preferences REPLACE on the server, so the
   form always sends the whole block. Health is sent only when the viewer
   can see it (health !== null): a role without health access sends no
   health key and the server keeps the stored allergies untouched.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Field, Input, Select, Textarea, useToast } from "../../../../../ui";
import { errText, gstApi, splitList } from "../lib";
import type { Profile } from "../types";

export default function ProfileEditor({
  profile, onCancel, onSaved,
}: {
  profile: Profile;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const canHealth = profile.preferences.health !== null;
  const [firstName, setFirstName] = useState(profile.first_name ?? "");
  const [language, setLanguage] = useState(profile.language === "ar" ? "ar" : "en");
  const [vip, setVip] = useState(profile.vip);
  const [tags, setTags] = useState(profile.preferences.tags.join(", "));
  const [notes, setNotes] = useState(profile.preferences.notes ?? "");
  const [allergies, setAllergies] = useState(profile.preferences.health?.allergies.join(", ") ?? "");
  const [healthNotes, setHealthNotes] = useState(profile.preferences.health?.notes ?? "");
  const [saving, setSaving] = useState(false);

  // A masked name (bullets) must not be written back as the new name.
  const nameMasked = /[*\u2022]/.test(profile.first_name ?? "");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    const preferences: Record<string, unknown> = { tags: splitList(tags).slice(0, 20), notes: notes.trim() || null };
    if (canHealth) preferences.health = { allergies: splitList(allergies).slice(0, 20), notes: healthNotes.trim() || null };
    const body: Record<string, unknown> = { language, vip, preferences };
    if (!nameMasked && firstName.trim() !== (profile.first_name ?? "")) body.first_name = firstName.trim() || null;
    try {
      await api.patch(gstApi(slug, `/profiles/${profile.id}`), body);
      toast.success("Profile saved");
      onSaved();
    } catch (err) {
      toast.error("Could not save", errText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="gst-form-grid" onSubmit={save}>
      <Field label="First name" htmlFor="gst-pe-name" hint={nameMasked ? "Hidden for your role; it stays as it is." : undefined}>
        <Input id="gst-pe-name" value={nameMasked ? "" : firstName} disabled={nameMasked} maxLength={80}
          onChange={e => setFirstName(e.target.value)} />
      </Field>
      <Field label="Language" htmlFor="gst-pe-lang">
        <Select id="gst-pe-lang" value={language} onChange={e => setLanguage(e.target.value as "en" | "ar")}
          options={[{ value: "en", label: "English" }, { value: "ar", label: "Arabic" }]} />
      </Field>
      <label className="gst-check gst-form-grid__wide">
        <input type="checkbox" checked={vip} onChange={e => setVip(e.target.checked)} />
        <span>VIP (their arrival raises a signal in the brief)</span>
      </label>
      <Field label="Preferences" htmlFor="gst-pe-tags" hint="Comma separated, e.g. extra pillows, high floor" className="gst-form-grid__wide">
        <Input id="gst-pe-tags" value={tags} onChange={e => setTags(e.target.value)} />
      </Field>
      <Field label="Notes" htmlFor="gst-pe-notes" className="gst-form-grid__wide">
        <Textarea id="gst-pe-notes" rows={2} maxLength={500} value={notes} onChange={e => setNotes(e.target.value)} />
      </Field>
      {canHealth ? (
        <>
          <Field label="Allergies" htmlFor="gst-pe-allergies" hint="Health data: shown only to F&B and front desk roles" className="gst-form-grid__wide">
            <Input id="gst-pe-allergies" value={allergies} onChange={e => setAllergies(e.target.value)} />
          </Field>
          <Field label="Health notes" htmlFor="gst-pe-hnotes" className="gst-form-grid__wide">
            <Textarea id="gst-pe-hnotes" rows={2} maxLength={500} value={healthNotes} onChange={e => setHealthNotes(e.target.value)} />
          </Field>
        </>
      ) : (
        <p className="gst-note gst-form-grid__wide">Allergies are hidden for your role and will be kept as they are.</p>
      )}
      <div className="cx-row gst-form-grid__wide" style={{ justifyContent: "flex-end" }}>
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" loading={saving}>Save</Button>
      </div>
    </form>
  );
}
