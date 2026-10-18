"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Create or edit one journey offer. Price is typed in major units and the
   server converts to minor units in the hotel's currency. Saving an edit
   clears the approval, which the form says before the owner saves.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useHotel } from "../../../../../../lib/hotel";
import { Button, Field, Input, Modal, Select, useToast } from "../../../../../../ui";
import { errText, jrnApi } from "../../lib";
import type { Offer } from "../../types";

const EM_DASH = "\u2014";

export default function OfferForm({ offer, onClose, onSaved }: { offer: Offer | null; onClose: () => void; onSaved: () => void }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const today = fmt.dayKey(new Date());
  const [step, setStep] = useState<"j3" | "j8">(offer?.step ?? "j8");
  const [titleEn, setTitleEn] = useState(offer?.title_en ?? "");
  const [titleAr, setTitleAr] = useState(offer?.title_ar ?? "");
  const [price, setPrice] = useState(offer?.price ?? "");
  const [pct, setPct] = useState(offer?.discount_pct ? String(Number(offer.discount_pct)) : "");
  const [prefix, setPrefix] = useState(offer?.code_prefix ?? "");
  const [from, setFrom] = useState(offer?.valid_from ?? today);
  const [to, setTo] = useState(offer?.valid_to ?? "");
  const [saving, setSaving] = useState(false);

  const dash = titleEn.includes(EM_DASH) || titleAr.includes(EM_DASH);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      step, title_en: titleEn.trim(), title_ar: titleAr.trim() || null,
      price: price === "" ? null : price, discount_pct: pct === "" ? null : pct,
      code_prefix: prefix.trim() || null, valid_from: from, valid_to: to,
    };
    setSaving(true);
    try {
      if (offer) await api.put(jrnApi(slug, `/offers/${offer.id}`), body);
      else await api.post(jrnApi(slug, "/offers"), body);
      toast.success(offer ? "Offer saved; approve it again to use it" : "Offer created; approve it to use it");
      onSaved();
    } catch (err) {
      toast.error("Could not save the offer", errText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open onClose={onClose} title={offer ? "Edit offer" : "New offer"} size="md">
      <form className="gst-form-grid" onSubmit={save}>
        <Field label="Used by" htmlFor="gst-of-step" className="gst-form-grid__wide">
          <Select id="gst-of-step" value={step} onChange={e => setStep(e.target.value as "j3" | "j8")}
            options={[{ value: "j3", label: "Pre-arrival offer (before check-in)" }, { value: "j8", label: "Rebooking offer (after check-out)" }]} />
        </Field>
        <Field label="Title (English)" htmlFor="gst-of-en" required className="gst-form-grid__wide">
          <Input id="gst-of-en" required minLength={2} maxLength={80} value={titleEn} onChange={e => setTitleEn(e.target.value)} />
        </Field>
        <Field label="Title (Arabic)" htmlFor="gst-of-ar" className="gst-form-grid__wide">
          <Input id="gst-of-ar" dir="rtl" lang="ar" maxLength={80} value={titleAr} onChange={e => setTitleAr(e.target.value)} />
        </Field>
        <Field label={`Price (${fmt.currency})`} htmlFor="gst-of-price">
          <Input id="gst-of-price" type="number" min={0} step="0.01" value={price} onChange={e => setPrice(e.target.value)} />
        </Field>
        <Field label="Discount %" htmlFor="gst-of-pct">
          <Input id="gst-of-pct" type="number" min={0.5} max={100} step="0.5" value={pct} onChange={e => setPct(e.target.value)} />
        </Field>
        <Field label="Valid from" htmlFor="gst-of-from" required>
          <Input id="gst-of-from" type="date" required value={from} onChange={e => setFrom(e.target.value)} />
        </Field>
        <Field label="Valid to" htmlFor="gst-of-to" required>
          <Input id="gst-of-to" type="date" required min={from} value={to} onChange={e => setTo(e.target.value)} />
        </Field>
        <Field label="Code prefix" htmlFor="gst-of-prefix" hint="Letters and digits, up to 8. Each guest gets their own code." className="gst-form-grid__wide">
          <Input id="gst-of-prefix" maxLength={8} pattern="[A-Za-z0-9]{1,8}" value={prefix} onChange={e => setPrefix(e.target.value.toUpperCase())} />
        </Field>
        {dash && <p className="gst-warn-box gst-form-grid__wide">Titles may not contain a long dash. Use a comma or a colon.</p>}
        {offer?.approved_at && <p className="gst-note gst-form-grid__wide">Saving changes withdraws the approval until you approve again.</p>}
        <div className="cx-row gst-form-grid__wide" style={{ justifyContent: "flex-end" }}>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" loading={saving} disabled={dash}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}
