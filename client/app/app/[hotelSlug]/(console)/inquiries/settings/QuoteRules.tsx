"use client";

/* Quote policy (PUT /quote-settings, merged on the server). Group and
   long-stay discounts put a quote below BAR, so those quotes always need
   approval (4.7). The group terms default to "Dubai norms UNVERIFIED" (spec
   assumption A12) and the label says so on every quote until the owner
   confirms or changes them. Percentages travel as Decimal strings. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Field, Input, Textarea, useToast } from "../../../../../ui";
import { errorText, inqPath } from "../lib";

export interface QuoteSettings {
  group_min_rooms: number;
  group_discount_pct: string;
  long_stay_min_nights: number;
  long_stay_discount_pct: string;
  group_deposits: { pct: string; days_before_arrival: number | null }[];
  group_cutoff_days: number;
  group_attrition_pct: string;
  group_norms_label: string;
  cancellation: { en?: string; ar?: string };
  inclusions: { en?: string; ar?: string };
  public_base_url: string;
}

const pctOk = (v: string) => /^\d{1,3}(\.\d{1,2})?$/.test(String(v)) && Number(v) <= 100;

export default function QuoteRules({ initial, canManage, onSaved }: {
  initial: QuoteSettings;
  canManage: boolean;
  onSaved: (v: QuoteSettings) => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [d, setD] = useState<QuoteSettings>(initial);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof QuoteSettings>(k: K, v: QuoteSettings[K]) => { setD(p => ({ ...p, [k]: v })); setDirty(true); };
  const setDep = (i: number, patch: Partial<QuoteSettings["group_deposits"][number]>) =>
    set("group_deposits", d.group_deposits.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  const ready = dirty && pctOk(d.group_discount_pct) && pctOk(d.long_stay_discount_pct) && pctOk(d.group_attrition_pct)
    && d.group_deposits.every(m => pctOk(m.pct) && Number(m.pct) > 0);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const out = await api.put<QuoteSettings>(inqPath(slug, "/quote-settings"), d);
      setD(out);
      setDirty(false);
      onSaved(out);
      toast.success("Quote rules saved", "New quotes use them; quotes already built keep their terms.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const ro = !canManage;
  return (
    <Card title="Quote rules" subtitle="How group and long-stay quotes are priced, and the terms every quote carries.">
      <div className="inq-form">
        <Field label="Group from (rooms)"><Input type="number" min={2} disabled={ro} value={String(d.group_min_rooms)} onChange={e => set("group_min_rooms", Number(e.target.value))} /></Field>
        <Field label="Group discount off BAR (%)"><Input inputMode="decimal" disabled={ro} value={d.group_discount_pct} onChange={e => set("group_discount_pct", e.target.value)} /></Field>
        <Field label="Long stay from (nights)"><Input type="number" min={7} disabled={ro} value={String(d.long_stay_min_nights)} onChange={e => set("long_stay_min_nights", Number(e.target.value))} /></Field>
        <Field label="Long-stay discount off BAR (%)"><Input inputMode="decimal" disabled={ro} value={d.long_stay_discount_pct} onChange={e => set("long_stay_discount_pct", e.target.value)} /></Field>
        <Field label="Group rooms released (days before arrival)"><Input type="number" min={0} max={365} disabled={ro} value={String(d.group_cutoff_days)} onChange={e => set("group_cutoff_days", Number(e.target.value))} /></Field>
        <Field label="Group attrition: rooms that must stay (%)"><Input inputMode="decimal" disabled={ro} value={d.group_attrition_pct} onChange={e => set("group_attrition_pct", e.target.value)} /></Field>
        <Field label="Group terms label" className="inq-full" hint="Printed on group quotes. Change it once you have confirmed these terms.">
          <Input disabled={ro} value={d.group_norms_label} maxLength={120} onChange={e => set("group_norms_label", e.target.value)} />
        </Field>
        <div className="inq-full" style={{ display: "grid", gap: 8 }}>
          <span className="inq-muted">Group deposits (cumulative share of the total; empty days = due at signing)</span>
          {d.group_deposits.map((m, i) => (
            <div key={i} className="inq-row">
              <Input aria-label="Cumulative percent" inputMode="decimal" disabled={ro} style={{ width: 90 }} value={m.pct}
                onChange={e => setDep(i, { pct: e.target.value })} />
              <span className="inq-muted">% by</span>
              <Input aria-label="Days before arrival" type="number" min={0} disabled={ro} style={{ width: 90 }}
                value={m.days_before_arrival == null ? "" : String(m.days_before_arrival)}
                onChange={e => setDep(i, { days_before_arrival: e.target.value === "" ? null : Number(e.target.value) })} />
              <span className="inq-muted">days before arrival</span>
              {!ro && d.group_deposits.length > 1 && (
                <Button size="sm" variant="ghost" onClick={() => set("group_deposits", d.group_deposits.filter((_, j) => j !== i))}>Remove</Button>
              )}
            </div>
          ))}
          {!ro && d.group_deposits.length < 6 && (
            <div><Button size="sm" variant="ghost" onClick={() => set("group_deposits", [...d.group_deposits, { pct: "100", days_before_arrival: 14 }])}>Add a milestone</Button></div>
          )}
        </div>
        <Field label="Cancellation (English)" className="inq-full">
          <Textarea rows={3} disabled={ro} value={d.cancellation.en ?? ""} onChange={e => set("cancellation", { ...d.cancellation, en: e.target.value })} />
        </Field>
        <Field label="Cancellation (Arabic)" className="inq-full">
          <Textarea rows={3} dir="rtl" lang="ar" disabled={ro} value={d.cancellation.ar ?? ""} onChange={e => set("cancellation", { ...d.cancellation, ar: e.target.value })} />
        </Field>
        <Field label="Included (English)" hint="For example: Breakfast for two, late check-out on request">
          <Input disabled={ro} value={d.inclusions.en ?? ""} onChange={e => set("inclusions", { ...d.inclusions, en: e.target.value })} />
        </Field>
        <Field label="Included (Arabic)">
          <Input dir="rtl" lang="ar" disabled={ro} value={d.inclusions.ar ?? ""} onChange={e => set("inclusions", { ...d.inclusions, ar: e.target.value })} />
        </Field>
      </div>
      {canManage && (
        <div className="inq-row" style={{ marginTop: 12 }}>
          <Button variant="primary" loading={busy} disabled={!ready} onClick={save}>Save quote rules</Button>
          {dirty && <Button variant="ghost" disabled={busy} onClick={() => { setD(initial); setDirty(false); }}>Discard</Button>}
          {error && <span className="inq-note inq-note--bad" role="alert">{error}</span>}
        </div>
      )}
    </Card>
  );
}
