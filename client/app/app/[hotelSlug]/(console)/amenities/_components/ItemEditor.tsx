"use client";

/* Add or edit one catalog item (amenities.manage). Money is typed in major
   units and sent as a string, so 1.25 AED never passes through a float on
   its way to fils. Unit cost INCLUDES excise (sweetened or energy drinks):
   that is what the hotel actually pays, and variance is valued with it.
   Deleting an item that already has issuance only deactivates it (server
   rule), so history keeps its names and costs. */

import { useState, type FormEvent } from "react";
import { ApiError, api, hotelPath } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Drawer, Field, Input, Select, useToast } from "../../../../../ui";
import CostHistory from "./CostHistory";
import { BASIS_LABELS, CATEGORY_LABELS } from "./lib";
import type { Item, ItemInput } from "./types";

interface Form {
  name_en: string; name_ar: string; key: string; sku: string; category: string; basis: string; std_qty: string;
  uom: string; unit_cost: string; excise_category: string; sup: boolean; aliases: string; chargeable: boolean;
  price: string; active: boolean;
}

function toForm(i: Item | null): Form {
  return {
    name_en: i?.name_en ?? "", name_ar: i?.name_ar ?? "", key: i?.key ?? "", sku: i?.sku ?? "",
    category: i?.category ?? "OTHER", basis: i?.basis ?? "PER_OCCUPIED_ROOM_NIGHT", std_qty: i?.std_qty ?? "1",
    uom: i?.uom ?? "unit", unit_cost: i?.unit_cost ?? "", excise_category: i?.excise_category ?? "NONE",
    sup: i?.is_single_use_plastic ?? false, aliases: (i?.aliases ?? []).join(", "), chargeable: i?.chargeable ?? false,
    price: i?.price ?? "", active: i?.active ?? true,
  };
}

const isMoney = (s: string) => s.trim() === "" || /^\d{1,7}(\.\d{1,3})?$/.test(s.trim());
const isQty = (s: string) => /^\d{1,3}(\.\d{1,2})?$/.test(s.trim());

export default function ItemEditor({ item, onClose, onSaved }: {
  item: Item | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [f, setF] = useState<Form>(() => toForm(item));
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [touched, setTouched] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF(prev => ({ ...prev, [k]: v }));

  const errors = {
    name_en: !f.name_en.trim() ? "A name is needed" : null,
    std_qty: f.basis !== "ON_REQUEST" && !isQty(f.std_qty) ? "A number from 0 to 999" : null,
    unit_cost: !isMoney(f.unit_cost) ? "An amount like 1.25" : null,
    price: f.chargeable && !isMoney(f.price) ? "An amount like 12" : null,
  };
  const invalid = Object.values(errors).some(Boolean);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (invalid) return;
    const body: ItemInput = {
      name_en: f.name_en.trim(), name_ar: f.name_ar.trim() || null, sku: f.sku.trim() || null,
      category: f.category as ItemInput["category"], basis: f.basis as ItemInput["basis"],
      std_qty: f.basis === "ON_REQUEST" ? "0" : f.std_qty.trim(), uom: f.uom.trim() || "unit",
      unit_cost: f.unit_cost.trim() || "0", excise_category: f.excise_category as ItemInput["excise_category"],
      is_single_use_plastic: f.sup, aliases: f.aliases.split(",").map(a => a.trim()).filter(Boolean),
      chargeable: f.chargeable, price: f.chargeable && f.price.trim() ? f.price.trim() : null, active: f.active,
    };
    if (!item && f.key.trim()) body.key = f.key.trim().toUpperCase();
    setBusy("save");
    try {
      if (item) await api.patch<Item>(hotelPath(slug, `/amenities/items/${item.id}`), { ...body, needs_review: false });
      else await api.post<Item>(hotelPath(slug, "/amenities/items"), body);
      toast.success(item ? "Item saved" : "Item added");
      onSaved();
    } catch (err) {
      toast.error("Not saved", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!item) return;
    setBusy("delete");
    try {
      const r = await api.del<{ deleted: boolean; deactivated: boolean }>(hotelPath(slug, `/amenities/items/${item.id}`));
      toast.success(r.deleted ? "Item deleted" : "Item deactivated",
        r.deleted ? undefined : "It has issuance history, so it is kept but hidden from entry.");
      onSaved();
    } catch (err) {
      toast.error("Could not delete", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const err = (k: keyof typeof errors) => (touched ? errors[k] : null);
  const footer = (
    <>
      {item && (
        <Button variant="danger" onClick={remove} loading={busy === "delete"} disabled={busy === "save"}>Delete</Button>
      )}
      <span className="amn-spacer" />
      <Button variant="ghost" onClick={onClose} disabled={busy !== null}>Cancel</Button>
      <Button variant="primary" type="submit" form="amn-item-form" loading={busy === "save"} disabled={busy === "delete"}>
        {item ? (item.needs_review ? "Save and confirm" : "Save") : "Add item"}
      </Button>
    </>
  );

  return (
    <Drawer open onClose={onClose} title={item ? `Edit ${item.name_en}` : "Add an item"} footer={footer} wide>
      <form id="amn-item-form" className="amn-form" onSubmit={save} noValidate>
        <Field label="Name (English)" required error={err("name_en")}>
          <Input value={f.name_en} maxLength={120} onChange={e => set("name_en", e.target.value)} invalid={Boolean(err("name_en"))} />
        </Field>
        <Field label="Name (Arabic)">
          <Input value={f.name_ar} maxLength={120} dir="rtl" lang="ar" onChange={e => set("name_ar", e.target.value)} />
        </Field>
        <Field label="Category">
          <Select value={f.category} onChange={e => set("category", e.target.value)}>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="SKU" hint="Your store or supplier code, used to match imports">
          <Input value={f.sku} maxLength={60} onChange={e => set("sku", e.target.value)} />
        </Field>
        <Field label="Basis" hint="What drives expected use">
          <Select value={f.basis} onChange={e => set("basis", e.target.value)}>
            {Object.entries(BASIS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        {f.basis !== "ON_REQUEST" && (
          <Field label="Standard quantity" hint="Per room-night, arrival or guest-night" error={err("std_qty")}>
            <Input inputMode="decimal" value={f.std_qty} onChange={e => set("std_qty", e.target.value)} invalid={Boolean(err("std_qty"))} />
          </Field>
        )}
        <Field label="Unit cost including excise" error={err("unit_cost")}>
          <Input inputMode="decimal" value={f.unit_cost} placeholder="0.00" onChange={e => set("unit_cost", e.target.value)}
                 invalid={Boolean(err("unit_cost"))} />
        </Field>
        <Field label="Excise category">
          <Select value={f.excise_category} onChange={e => set("excise_category", e.target.value)}>
            <option value="NONE">None</option>
            <option value="SWEETENED">Sweetened drink</option>
            <option value="ENERGY">Energy drink</option>
          </Select>
        </Field>
        <Field label="Unit" hint="piece, pair, bottle...">
          <Input value={f.uom} maxLength={20} onChange={e => set("uom", e.target.value)} />
        </Field>
        {!item && (
          <Field label="Key" hint="Optional; made from the name when blank">
            <Input value={f.key} maxLength={48} onChange={e => set("key", e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
          </Field>
        )}
        <Field label="Other names" hint="Comma separated, e.g. toothbrush, dental set. Used to match guest requests and imports."
               className="amn-span2">
          <Input value={f.aliases} onChange={e => set("aliases", e.target.value)} />
        </Field>
        <label className="amn-check"><input type="checkbox" checked={f.sup} onChange={e => set("sup", e.target.checked)} />
          Single-use plastic</label>
        <label className="amn-check"><input type="checkbox" checked={f.active} onChange={e => set("active", e.target.checked)} />
          Active</label>
        <label className="amn-check"><input type="checkbox" checked={f.chargeable} onChange={e => set("chargeable", e.target.checked)} />
          Charged to the guest (minibar)</label>
        {f.chargeable && (
          <Field label="Guest price" error={err("price")}>
            <Input inputMode="decimal" value={f.price} onChange={e => set("price", e.target.value)} invalid={Boolean(err("price"))} />
          </Field>
        )}
      </form>
      {item && <CostHistory itemId={item.id} />}
    </Drawer>
  );
}
