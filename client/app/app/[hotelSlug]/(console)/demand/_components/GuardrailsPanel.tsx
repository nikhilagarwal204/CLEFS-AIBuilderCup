"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Guardrails and BAR: the floor and ceiling every rate suggestion (and the
   Inquiry quote builder) stays inside, per room type, month and weekday;
   the most specific row wins. The biggest single move is max_step_pct
   (default 15%). The BAR sheet import feeds the calendar, the suggestions
   and the APPLIED check. Rows upsert by (room type, month, weekday).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { ApiError, api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Button, Card, EmptyState, Field, Input, Select, Skeleton, Table, useToast, type Column } from "../../../../../ui";
import ImportModal from "./ImportModal";
import { DOW_LABEL, guardrailBody, guardrailScope, MONTH_LABEL, validateGuardrail, type GuardrailForm } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { Guardrail } from "../_lib/types";

const EMPTY: GuardrailForm = { room_type_code: "*", month: "", dow: "", floor: "", ceiling: "", step: "15" };

interface BarImport { written: number; changed: number; nights: number; room_types: string[] }

export default function GuardrailsPanel({ slug }: { slug: string }) {
  const fmt = useFormat();
  const toast = useToast();
  const g = useApi<{ currency: string; rows: Guardrail[] }>(drApi(slug, "/guardrails"), [slug]);
  const [f, setF] = useState<GuardrailForm>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const ccy = g.data?.currency;
  const money = (v: string) => fmt.money(v, { maximumFractionDigits: 0, currency: ccy });
  const set = <K extends keyof GuardrailForm>(k: K, v: string) => setF(prev => ({ ...prev, [k]: v }));

  const save = async () => {
    const errs = validateGuardrail(f);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy("save");
    try {
      g.setData(await api.put<{ currency: string; rows: Guardrail[] }>(drApi(slug, "/guardrails"), { rows: [guardrailBody(f)] }));
      toast.success("Guardrail saved", guardrailScope({ room_type_code: f.room_type_code.trim() || "*", month: f.month ? Number(f.month) : null, dow: f.dow ? Number(f.dow) : null }));
      setF(EMPTY);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) setErrors(err.fieldErrors);
      toast.error("Could not save the guardrail", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (row: Guardrail) => {
    setBusy(`del-${row.id}`);
    try {
      await api.del(drApi(slug, `/guardrails/${row.id}`));
      g.setData(prev => ({ currency: prev?.currency ?? "", rows: (prev?.rows ?? []).filter(r => r.id !== row.id) }));
      toast.success("Guardrail removed", guardrailScope(row));
    } catch (err) {
      toast.error("Could not remove it", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const edit = (r: Guardrail) => setF({
    room_type_code: r.room_type_code, month: r.month ? String(r.month) : "", dow: r.dow ? String(r.dow) : "",
    floor: String(r.floor), ceiling: String(r.ceiling), step: String(r.max_step_pct),
  });

  const columns: Column<Guardrail>[] = [
    { key: "scope", header: "Applies to", render: r => guardrailScope(r) },
    { key: "floor", header: "Floor", align: "right", render: r => money(r.floor) },
    { key: "ceiling", header: "Ceiling", align: "right", render: r => money(r.ceiling) },
    { key: "step", header: "Max move", align: "right", render: r => `${Number(r.max_step_pct)}%`, hideOnMobile: true },
    {
      key: "act", header: "", align: "right",
      render: r => (
        <span className="cx-row" style={{ gap: 4, justifyContent: "flex-end" }}>
          <Button size="sm" variant="ghost" onClick={() => edit(r)}>Edit</Button>
          <Button size="sm" variant="ghost" loading={busy === `del-${r.id}`} onClick={() => remove(r)}>Remove</Button>
        </span>
      ),
    },
  ];

  return (
    <Card
      title="Rate guardrails"
      subtitle="Suggestions never go below the floor or above the ceiling, and never move more than the max step at once. Without a row, the bounds are your BAR -10% to +30%."
      actions={<Button size="sm" onClick={() => setImporting(true)}>Import BAR sheet</Button>}
    >
      {g.error ? (
        <EmptyState title="Could not load guardrails" body={errorText(g.error)} action={<Button onClick={g.reload}>Try again</Button>} />
      ) : g.loading && !g.data ? (
        <Skeleton lines={3} height={18} />
      ) : (
        <Table<Guardrail> columns={columns} rows={g.data?.rows ?? []} rowKey={r => r.id} caption="Rate guardrails"
                          empty="No guardrails yet. Add one for all rooms first, then narrow by month or weekday." />
      )}

      <div className="dr-section-title">Add or update a row</div>
      <div className="dr-toolbar">
        <Field label="Room type" hint="* = all" error={errors.room_type_code}>
          <Input value={f.room_type_code} maxLength={32} style={{ width: 96 }} onChange={e => set("room_type_code", e.target.value)} />
        </Field>
        <Field label="Month">
          <Select value={f.month} onChange={e => set("month", e.target.value)}>
            <option value="">Any</option>
            {MONTH_LABEL.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </Select>
        </Field>
        <Field label="Weekday">
          <Select value={f.dow} onChange={e => set("dow", e.target.value)}>
            <option value="">Any</option>
            {DOW_LABEL.map((d, i) => <option key={d} value={i + 1}>{d}</option>)}
          </Select>
        </Field>
        <Field label={`Floor${ccy ? ` (${ccy})` : ""}`} error={errors.floor}>
          <Input inputMode="decimal" style={{ width: 110 }} value={f.floor} invalid={Boolean(errors.floor)} onChange={e => set("floor", e.target.value)} />
        </Field>
        <Field label={`Ceiling${ccy ? ` (${ccy})` : ""}`} error={errors.ceiling}>
          <Input inputMode="decimal" style={{ width: 110 }} value={f.ceiling} invalid={Boolean(errors.ceiling)} onChange={e => set("ceiling", e.target.value)} />
        </Field>
        <Field label="Max move %" error={errors.step}>
          <Input inputMode="decimal" style={{ width: 80 }} value={f.step} invalid={Boolean(errors.step)} onChange={e => set("step", e.target.value)} />
        </Field>
        <Button variant="primary" loading={busy === "save"} onClick={save}>Save row</Button>
      </div>

      <ImportModal<BarImport>
        open={importing}
        onClose={() => setImporting(false)}
        title="Import your BAR"
        intro="A CSV with a date, a room type code and a rate per row, or a date column plus one column per room type code. Rates are in your hotel currency."
        endpoint={drApi(slug, "/bar/import")}
        accept=".csv,text/csv"
        describe={r => `${r.written} rates over ${r.nights} nights (${r.changed} changed) for ${r.room_types.join(", ") || "no room types"}.`}
        onDone={() => undefined}
      />
    </Card>
  );
}
