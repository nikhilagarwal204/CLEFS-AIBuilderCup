"use client";

/* The hotel's tax profile: the two facts the fee rules cannot know on their
   own. The property category picks the Tourism Dirham rate (a 4-star hotel
   owes AED 15 a room-night, a deluxe apartment AED 15, a guest house AED 7),
   and the stacking convention says how the PMS builds a price from the room
   rate. Profiles are date-effective: saving writes a new row from the chosen
   date, so last year's stays keep the convention they were sold under. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Badge, Button, Field, Input, Segmented, Select, useToast } from "../../../../../ui";
import { errorText } from "../../../../../ingest";
import { CONVENTION_HELP, PROPERTY_CATEGORIES, taxPath, type TaxProfile, type TaxProfileBody } from "./api";

type Conv = "A" | "B" | "C";
const BEDROOM_OPTIONS = [
  { value: "", label: "Follow the rule (per bedroom in Dubai)" },
  { value: "yes", label: "Per bedroom" },
  { value: "no", label: "Per room" },
];

export interface TaxProfileCardProps {
  slug: string;
  profile: TaxProfile | null;
  today: string;
  canWrite: boolean;
  onSaved: () => void;
}

export function TaxProfileCard({ slug, profile, today, canWrite, onSaved }: TaxProfileCardProps) {
  const toast = useToast();
  const [category, setCategory] = useState(profile?.property_category ?? "");
  const [convention, setConvention] = useState<Conv>((profile?.convention as Conv) ?? "A");
  const [trn, setTrn] = useState(profile?.trn ?? "");
  const [bedroom, setBedroom] = useState(profile?.td_per_bedroom == null ? "" : profile.td_per_bedroom ? "yes" : "no");
  const [from, setFrom] = useState(today);
  const [saving, setSaving] = useState(false);
  const trnOk = !trn || /^\d{15}$/.test(trn.replace(/\s/g, ""));

  const save = async () => {
    if (!category) return;
    setSaving(true);
    try {
      const body: TaxProfileBody = {
        effective_from: from || today,
        property_category: category,
        convention,
        trn: trn.replace(/\s/g, "") || null,
        td_per_bedroom: bedroom === "" ? null : bedroom === "yes",
      };
      await api.put(taxPath(slug, "/profile"), body);
      toast.success("Tax profile saved", `In force from ${body.effective_from}.`);
      onSaved();
    } catch (err) {
      toast.error("Couldn't save the tax profile", errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const dis = !canWrite || saving;
  return (
    <div className="cx-stack" style={{ gap: 14 }}>
      <div className="cx-row">
        {profile?.emirate && <Badge tone="info">{profile.emirate}</Badge>}
        {profile?.currency && <Badge tone="neutral">{profile.currency}</Badge>}
        {profile?.source === "hotel" && <Badge tone="warn">Read from the hotel profile; save to confirm</Badge>}
        {profile?.source === "profile" && profile.effective_from && (
          <Badge tone="ok">Confirmed, in force from {profile.effective_from}</Badge>
        )}
      </div>
      <div className="cx-grid cx-grid--2">
        <Field label="Property category" hint="Sets the Tourism Dirham rate." required>
          <Select value={category} disabled={dis} placeholder="Choose a category"
                  options={PROPERTY_CATEGORIES} onChange={e => setCategory(e.target.value)} />
        </Field>
        <Field label="Tax registration number (TRN)" hint="15 digits, printed on your tax invoices."
               error={trnOk ? undefined : "A TRN has 15 digits."}>
          <Input value={trn} disabled={dis} inputMode="numeric" invalid={!trnOk}
                 onChange={e => setTrn(e.target.value.replace(/[^\d\s]/g, "").slice(0, 20))} />
        </Field>
        <Field label="Tourism Dirham counted">
          <Select value={bedroom} disabled={dis} options={BEDROOM_OPTIONS} onChange={e => setBedroom(e.target.value)} />
        </Field>
        <Field label="Changes apply to stays from">
          <Input type="date" value={from} disabled={dis} onChange={e => setFrom(e.target.value)} />
        </Field>
      </div>
      <Field label="How your PMS stacks fees on the room rate">
        <Segmented ariaLabel="Stacking convention" value={convention}
                   options={(["A", "B", "C"] as const).map(c => ({ value: c, label: `Convention ${c}`, disabled: dis }))}
                   onChange={v => setConvention(v)} />
      </Field>
      <p className="tax-note">{CONVENTION_HELP[convention]} Check it against one folio: the preview below shows the split each convention gives.</p>
      {canWrite && (
        <div className="cx-row" style={{ justifyContent: "flex-end" }}>
          <Button loading={saving} disabled={!category || !trnOk} onClick={save}>Save tax profile</Button>
        </div>
      )}
    </div>
  );
}
