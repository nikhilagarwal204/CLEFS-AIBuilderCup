"use client";

/* Correct what extraction read (PATCH /{id}). Only changed fields are sent
   (the route merges with exclude_unset, so an untouched field keeps its
   value and a cleared one is sent as null on purpose). Segment is never
   sent empty: the column is NOT NULL and "unknown" is the explicit choice. */

import { useState, type FormEvent } from "react";
import { api } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { Button, Field, Input, Select, useToast } from "../../../../ui";
import { errorText, inqPath, SEGMENT_LABEL, SEGMENTS } from "./lib";
import type { InquiryDetail, Segment } from "./types";

interface Draft {
  segment: Segment;
  is_rfp: boolean;
  check_in: string;
  check_out: string;
  dates_flexible: boolean;
  rooms: string;
  adults: string;
  children: string;
  room_type_code: string;
  language: string;
  company: string;
  contact_name: string;
}

const fromRow = (r: InquiryDetail): Draft => ({
  segment: r.segment,
  is_rfp: r.is_rfp,
  check_in: r.check_in ?? "",
  check_out: r.check_out ?? "",
  dates_flexible: !!r.dates_flexible,
  rooms: r.rooms == null ? "" : String(r.rooms),
  adults: r.adults == null ? "" : String(r.adults),
  children: r.children == null ? "" : String(r.children),
  room_type_code: r.room_type_code ?? "",
  language: r.language ?? "",
  company: r.company ?? "",
  contact_name: r.contact_name ?? "",
});

const NUMBER_KEYS = ["rooms", "adults", "children"] as const;
const TEXT_KEYS = ["check_in", "check_out", "room_type_code", "language", "company", "contact_name"] as const;

/** The PATCH body: changed fields only; "" becomes null (a deliberate clear). */
export function patchBody(before: Draft, after: Draft): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (after.segment !== before.segment) out.segment = after.segment;
  if (after.is_rfp !== before.is_rfp) out.is_rfp = after.is_rfp;
  if (after.dates_flexible !== before.dates_flexible) out.dates_flexible = after.dates_flexible;
  for (const k of NUMBER_KEYS) {
    if (after[k] !== before[k]) out[k] = after[k].trim() === "" ? null : Number(after[k]);
  }
  for (const k of TEXT_KEYS) {
    if (after[k] !== before[k]) out[k] = after[k].trim() === "" ? null : after[k].trim();
  }
  return out;
}

export default function DetailsForm({ row, onCancel, onSaved }: {
  row: InquiryDetail;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [initial] = useState(() => fromRow(row));
  const [d, setD] = useState<Draft>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD(prev => ({ ...prev, [k]: v }));
  const datesBad = !!(d.check_in && d.check_out && d.check_out <= d.check_in);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body = patchBody(initial, d);
    if (!Object.keys(body).length) { onCancel(); return; }
    setBusy(true);
    setError(null);
    try {
      await api.patch(inqPath(slug, `/${row.id}`), body);
      toast.success("Details saved", "The reply deadline and money at risk were recalculated.");
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="inq-form">
      <Field label="Segment">
        <Select value={d.segment} onChange={e => set("segment", e.target.value as Segment)}>
          {SEGMENTS.map(s => <option key={s} value={s}>{SEGMENT_LABEL[s]}</option>)}
        </Select>
      </Field>
      <Field label="Language">
        <Select value={d.language} onChange={e => set("language", e.target.value)}>
          <option value="">Not set</option>
          <option value="en">English</option>
          <option value="ar">Arabic</option>
          {d.language && !["en", "ar"].includes(d.language) && <option value={d.language}>{d.language}</option>}
        </Select>
      </Field>
      <Field label="Check-in"><Input type="date" value={d.check_in} onChange={e => set("check_in", e.target.value)} /></Field>
      <Field label="Check-out" error={datesBad ? "Check-out must be after check-in" : undefined}>
        <Input type="date" value={d.check_out} min={d.check_in || undefined} invalid={datesBad}
          onChange={e => set("check_out", e.target.value)} />
      </Field>
      <Field label="Rooms"><Input type="number" min={1} max={200} value={d.rooms} onChange={e => set("rooms", e.target.value)} /></Field>
      <Field label="Room type code"><Input value={d.room_type_code} maxLength={40} onChange={e => set("room_type_code", e.target.value)} /></Field>
      <Field label="Adults"><Input type="number" min={1} max={2000} value={d.adults} onChange={e => set("adults", e.target.value)} /></Field>
      <Field label="Children"><Input type="number" min={0} max={2000} value={d.children} onChange={e => set("children", e.target.value)} /></Field>
      <Field label="Guest name"><Input value={d.contact_name} maxLength={200} onChange={e => set("contact_name", e.target.value)} /></Field>
      <Field label="Company"><Input value={d.company} maxLength={200} onChange={e => set("company", e.target.value)} /></Field>
      <label className="inq-row inq-full">
        <input type="checkbox" checked={d.dates_flexible} onChange={e => set("dates_flexible", e.target.checked)} />
        Dates are flexible
      </label>
      <label className="inq-row inq-full">
        <input type="checkbox" checked={d.is_rfp} onChange={e => set("is_rfp", e.target.checked)} />
        This is a formal RFP (corporate or group tender)
      </label>
      {error && <p className="inq-note inq-note--bad inq-full" role="alert">{error}</p>}
      <div className="inq-row inq-full">
        <Button type="submit" variant="primary" loading={busy} disabled={datesBad}>Save</Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </form>
  );
}
