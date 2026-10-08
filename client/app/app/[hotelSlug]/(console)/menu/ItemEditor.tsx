"use client";

/* One menu item: English and Arabic copy, price in the hotel currency,
   optional hours inside the outlet's hours, dietary tags, alcohol flag,
   prep time and a photo. The price is typed in major units ("45.00") and
   sent as a string; the server stores minor units, so there is no float
   anywhere between the keyboard and the ledger. */

import { useState } from "react";
import { api, hotelPath } from "../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../lib/format";
import {
  Button, Drawer, Field, FileDrop, Input, ProgressBar, Select, Textarea, useToast, useUpload,
} from "../../../../ui";
import { DayPicker } from "./HoursEditor";
import { ALL_DAYS, DIETARY, HHMM, errText, normalisePrice } from "./lib";
import type { DietaryTag, MenuData, MenuItem } from "./types";

// ─── Form state ──────────────────────────────────────────────────────────

interface ItemForm {
  outlet_id: string;
  name_en: string;
  name_ar: string;
  desc_en: string;
  desc_ar: string;
  category: string;
  category_ar: string;
  sku: string;
  price: string;
  image_file_id: string | null;
  limit_hours: boolean;
  available_from: string;
  available_to: string;
  days_mask: number;
  dietary: DietaryTag[];
  alcohol: boolean;
  prep_min: string;
  active: boolean;
  sort: string;
}

function toForm(i: MenuItem | null, outletId: number | undefined): ItemForm {
  return {
    outlet_id: String(i?.outlet_id ?? outletId ?? ""),
    name_en: i?.name_en ?? "",
    name_ar: i?.name_ar ?? "",
    desc_en: i?.desc_en ?? "",
    desc_ar: i?.desc_ar ?? "",
    category: i?.category ?? "",
    category_ar: i?.category_ar ?? "",
    sku: i?.sku ?? "",
    price: i?.price ?? "",
    image_file_id: i?.image_file_id ?? null,
    limit_hours: Boolean(i?.available_from && i?.available_to),
    available_from: i?.available_from ?? "",
    available_to: i?.available_to ?? "",
    days_mask: i?.days_mask ?? ALL_DAYS,
    dietary: i?.dietary ?? [],
    alcohol: i?.alcohol ?? false,
    prep_min: i?.prep_min != null ? String(i.prep_min) : "",
    active: i?.active ?? true,
    sort: String(i?.sort ?? 0),
  };
}

const nul = (s: string) => (s.trim() ? s.trim() : null);

// ─── Editor ──────────────────────────────────────────────────────────────

export default function ItemEditor({ data, item, defaultOutletId, onClose, onSaved }: {
  data: MenuData;
  item: MenuItem | null;
  defaultOutletId?: number;
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const upload = useUpload(data.slug, "menu.image");
  const [f, setF] = useState<ItemForm>(() => toForm(item, defaultOutletId));
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const set = (patch: Partial<ItemForm>) => setF(prev => ({ ...prev, ...patch }));

  const currency = item?.currency?.trim() || fmt.currency;
  const price = normalisePrice(f.price, currencyDigits(currency));
  const hoursOk = !f.limit_hours || (HHMM.test(f.available_from) && HHMM.test(f.available_to) && f.available_from !== f.available_to);
  const prepOk = f.prep_min === "" || /^\d{1,3}$/.test(f.prep_min);
  const valid = Boolean(f.outlet_id) && f.name_en.trim() !== "" && price !== null && hoursOk && prepOk && f.days_mask > 0;

  const toggleDiet = (t: DietaryTag) =>
    set({ dietary: f.dietary.includes(t) ? f.dietary.filter(d => d !== t) : [...f.dietary, t] });

  const onPhoto = async (files: File[]) => {
    const ref = await upload.upload(files[0]);
    if (ref) set({ image_file_id: ref.id });
  };

  const save = async () => {
    if (!valid || price === null) return;
    setBusy(true);
    const body = {
      outlet_id: Number(f.outlet_id),
      name_en: f.name_en.trim(),
      name_ar: nul(f.name_ar),
      desc_en: nul(f.desc_en),
      desc_ar: nul(f.desc_ar),
      category: nul(f.category),
      category_ar: nul(f.category_ar),
      sku: nul(f.sku),
      price,
      image_file_id: f.image_file_id,
      available_from: f.limit_hours ? f.available_from : null,
      available_to: f.limit_hours ? f.available_to : null,
      days_mask: f.days_mask,
      dietary: f.dietary,
      alcohol: f.alcohol,
      prep_min: f.prep_min === "" ? null : Number(f.prep_min),
      active: f.active,
      sort: Number(f.sort) || 0,
    };
    try {
      if (item) await api.patch(hotelPath(data.slug, `/menu/items/${item.id}`), body);
      else await api.post(hotelPath(data.slug, "/menu/items"), body);
      onSaved(item ? "Item saved" : "Item added");
    } catch (err) {
      toast.error("Could not save the item", errText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!item) return;
    setBusy(true);
    try {
      await api.del(hotelPath(data.slug, `/menu/items/${item.id}`));
      onSaved("Item deleted");
    } catch (err) {
      toast.error("Could not delete the item", errText(err));
      setBusy(false);
    }
  };

  return (
    <Drawer
      open
      wide
      onClose={onClose}
      title={item ? `Edit ${item.name_en}` : "Add item"}
      footer={
        <div className="ups-actions" style={{ width: "100%" }}>
          {item && !confirmDelete && <Button variant="ghost" onClick={() => setConfirmDelete(true)} disabled={busy}>Delete</Button>}
          {item && confirmDelete && <Button variant="danger" onClick={remove} loading={busy}>Confirm delete</Button>}
          <span className="ups-spacer" />
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={busy} disabled={!valid || upload.uploading}>Save</Button>
        </div>
      }
    >
      <div className="ups-form ups-form--2">
        <Field label="Name (English)" required>
          <Input value={f.name_en} maxLength={160} onChange={e => set({ name_en: e.target.value })} />
        </Field>
        <Field label="Name (Arabic)">
          <Input className="ups-rtl" dir="rtl" lang="ar" value={f.name_ar} maxLength={160}
                 onChange={e => set({ name_ar: e.target.value })} />
        </Field>
        <Field label="Description (English)">
          <Textarea rows={3} value={f.desc_en} maxLength={1000} onChange={e => set({ desc_en: e.target.value })} />
        </Field>
        <Field label="Description (Arabic)">
          <Textarea className="ups-rtl" dir="rtl" lang="ar" rows={3} value={f.desc_ar} maxLength={1000}
                    onChange={e => set({ desc_ar: e.target.value })} />
        </Field>
        <Field label="Section (English)" hint="Groups items on the guest menu, e.g. Breakfast.">
          <Input value={f.category} maxLength={80} onChange={e => set({ category: e.target.value })} />
        </Field>
        <Field label="Section (Arabic)">
          <Input className="ups-rtl" dir="rtl" lang="ar" value={f.category_ar} maxLength={80}
                 onChange={e => set({ category_ar: e.target.value })} />
        </Field>
        <Field label={`Price (${currency})`} required
               hint={price !== null ? fmt.money(price, { currency }) : "As printed on the menu."}
               error={f.price && price === null ? "Enter an amount such as 45 or 45.50" : undefined}>
          <Input inputMode="decimal" value={f.price} placeholder="45.00" onChange={e => set({ price: e.target.value })} />
        </Field>
        <Field label="Outlet" required>
          <Select value={f.outlet_id} onChange={e => set({ outlet_id: e.target.value })} placeholder="Choose an outlet"
                  options={data.outlets.map(o => ({ value: String(o.id), label: o.name_en }))} />
        </Field>
        <Field label="SKU" hint="Optional. Lets a CSV re-import update this item.">
          <Input value={f.sku} maxLength={60} onChange={e => set({ sku: e.target.value })} />
        </Field>
        <Field label="Preparation time (minutes)" error={prepOk ? undefined : "Whole minutes, up to 600"}>
          <Input inputMode="numeric" value={f.prep_min} placeholder="20" onChange={e => set({ prep_min: e.target.value })} />
        </Field>
        <ItemExtras f={f} set={set} toggleDiet={toggleDiet} hoursOk={hoursOk} />
        <div className="ups-span cx-field" role="group" aria-labelledby="ups-item-photo">
          <span className="cx-label" id="ups-item-photo">Photo</span>
          {f.image_file_id ? (
            <div className="cx-row">
              <span className="cx-muted">Photo attached.</span>
              <Button size="sm" variant="ghost" onClick={() => { set({ image_file_id: null }); upload.reset(); }}>Remove photo</Button>
            </div>
          ) : (
            <FileDrop accept="image/jpeg,image/png,image/webp" maxBytes={5 * 1024 * 1024} onFiles={onPhoto}
                      disabled={upload.uploading} title="Drop a photo or tap to choose" hint="JPG, PNG or WebP, up to 5 MB."
                      onReject={reason => toast.error("Photo not accepted", reason)} />
          )}
          {upload.uploading && <ProgressBar value={upload.progress} />}
          {upload.error && <span className="cx-error-text">{upload.error.message}</span>}
        </div>
      </div>
    </Drawer>
  );
}

// ─── Hours, days, dietary, flags ─────────────────────────────────────────

function ItemExtras({ f, set, toggleDiet, hoursOk }: {
  f: ItemForm;
  set: (patch: Partial<ItemForm>) => void;
  toggleDiet: (t: DietaryTag) => void;
  hoursOk: boolean;
}) {
  return (
    <>
      <div className="ups-span cx-field" role="group" aria-labelledby="ups-item-hours">
        <span className="cx-label" id="ups-item-hours">When it can be ordered</span>
        <label className="ups-check">
          <input type="checkbox" checked={f.limit_hours} onChange={e => set({ limit_hours: e.target.checked })} />
          Only between set times (inside the outlet&apos;s hours)
        </label>
        {f.limit_hours && (
          <div className="ups-window__row">
            <Input type="time" aria-label="From" value={f.available_from} invalid={!hoursOk}
                   onChange={e => set({ available_from: e.target.value })} />
            <span className="cx-faint">to</span>
            <Input type="time" aria-label="To" value={f.available_to} invalid={!hoursOk}
                   onChange={e => set({ available_to: e.target.value })} />
          </div>
        )}
        <DayPicker mask={f.days_mask} onChange={m => set({ days_mask: m })} label="Days it is offered" />
        {f.days_mask === 0 && <span className="cx-error-text">Pick at least one day.</span>}
      </div>
      <div className="ups-span cx-field" role="group" aria-labelledby="ups-item-diet">
        <span className="cx-label" id="ups-item-diet">Dietary</span>
        <div className="ups-chips">
          {DIETARY.map(d => (
            <button key={d.value} type="button" className="ups-chip" aria-pressed={f.dietary.includes(d.value)}
                    onClick={() => toggleDiet(d.value)}>
              {d.label}
            </button>
          ))}
        </div>
      </div>
      <div className="ups-span cx-row" style={{ gap: 18 }}>
        <label className="ups-check">
          <input type="checkbox" checked={f.alcohol} onChange={e => set({ alcohol: e.target.checked })} />
          Contains alcohol
        </label>
        <label className="ups-check">
          <input type="checkbox" checked={f.active} onChange={e => set({ active: e.target.checked })} />
          Active (shown to guests)
        </label>
      </div>
      {f.alcohol && (
        <div className="ups-span ups-sub">
          Alcohol stays off the guest menu unless alcohol orders are switched on in Settings, Modules, Menu.
        </div>
      )}
    </>
  );
}
