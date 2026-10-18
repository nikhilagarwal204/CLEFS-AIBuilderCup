"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Create or edit a pilot. Success criteria pick from the metric catalog
   (GET /hotels/{slug}/metrics), so a criterion can only name a metric the
   scorecard can actually compute; the server re-checks (400 unknown_metric).
   The conversion price is typed in major units of the hotel currency and
   sent as a string, so "4999.50" never passes through a float.
   Editing criteria or the baseline window re-captures the baseline on the
   server, so the form says so before the owner saves.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Button, Field, Input, Modal, Select, useToast } from "../../../../../ui";
import { COMPARATOR_LABEL, errText, moduleLabel, MODULE_LABEL, trustApi } from "../lib";
import type { Comparator, Criterion, MetricInfo, Pilot } from "../types";

const PILOT_MODULES = Object.keys(MODULE_LABEL).filter(m => m !== "core" && m !== "trust");
const COMPARATORS = Object.keys(COMPARATOR_LABEL) as Comparator[];

interface Row { metric_key: string; comparator: Comparator; target: string; weight: string; label: string }

const blankRow = (metric = ""): Row => ({ metric_key: metric, comparator: "gte", target: "", weight: "1", label: "" });

function toRows(c: Criterion[] | undefined): Row[] {
  if (!c?.length) return [blankRow()];
  return c.map(x => ({
    metric_key: x.metric_key, comparator: x.comparator, target: String(x.target ?? ""),
    weight: String(x.weight ?? "1"), label: x.label ?? "",
  }));
}

const isNum = (s: string) => s.trim() !== "" && Number.isFinite(Number(s));

export default function PilotForm({ slug, pilot, onClose, onSaved }: {
  slug: string;
  pilot?: Pilot;
  onClose: () => void;
  onSaved: (p: Pilot) => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const metrics = useApi<MetricInfo[]>(hotelPath(slug, "/metrics"), [slug]);

  const [name, setName] = useState(pilot?.name ?? "");
  const [modules, setModules] = useState<string[]>(pilot?.modules ?? []);
  const [start, setStart] = useState(pilot?.start_date ?? "");
  const [end, setEnd] = useState(pilot?.end_date ?? "");
  const [bStart, setBStart] = useState(pilot?.baseline_start ?? "");
  const [bEnd, setBEnd] = useState(pilot?.baseline_end ?? "");
  const [price, setPrice] = useState(pilot?.conversion_price ?? "");
  const [period, setPeriod] = useState<"month" | "year">(pilot?.terms?.billing_period ?? "month");
  const [threshold, setThreshold] = useState(String(Number(pilot?.terms?.on_track_threshold ?? 0.7) * 100));
  const [rows, setRows] = useState<Row[]>(() => toRows(pilot?.success_criteria));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const rowsOk = rows.length > 0 && rows.every(r => r.metric_key && isNum(r.target) && isNum(r.weight) && Number(r.weight) > 0);
  const datesOk = !!start && !!end && start <= end;
  const baselineOk = (!bStart && !bEnd) || (!!bStart && !!bEnd && bStart <= bEnd && bEnd < start);
  const priceOk = !price.trim() || (isNum(price) && Number(price) >= 0);
  const thrOk = isNum(threshold) && Number(threshold) >= 0 && Number(threshold) <= 100;
  const valid = name.trim() && modules.length > 0 && datesOk && baselineOk && priceOk && thrOk && rowsOk;

  // ─── Save ─────────────────────────────────────────────────────────────

  function body(): Record<string, unknown> {
    const criteria: Criterion[] = rows.map(r => ({
      metric_key: r.metric_key, comparator: r.comparator, target: r.target.trim(), weight: r.weight.trim(),
      label: r.label.trim() || null,
    }));
    // Other terms keys (set by Clefs staff) survive an edit untouched.
    const terms = { ...(pilot?.terms ?? {}), billing_period: period, on_track_threshold: String(Number(threshold) / 100) };
    const full: Record<string, unknown> = {
      name: name.trim(), modules, start_date: start, end_date: end, success_criteria: criteria,
      baseline_start: bStart || null, baseline_end: bEnd || null,
      conversion_price: price.trim() ? price.trim() : null, terms,
    };
    if (!pilot) return full;
    // PATCH sends only what changed: the server re-captures the baseline
    // whenever criteria or the baseline window are present, and a no-op
    // re-capture would move the goalposts mid-pilot.
    const before: Record<string, unknown> = {
      name: pilot.name, modules: pilot.modules, start_date: pilot.start_date, end_date: pilot.end_date,
      success_criteria: toRows(pilot.success_criteria).map(r => ({
        metric_key: r.metric_key, comparator: r.comparator, target: r.target, weight: r.weight, label: r.label || null,
      })),
      baseline_start: pilot.baseline_start, baseline_end: pilot.baseline_end,
      conversion_price: pilot.conversion_price, terms: pilot.terms ?? {},
    };
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(full)) {
      const same = k === "conversion_price"
        ? (v === null ? before[k] === null : before[k] !== null && Number(v) === Number(before[k]))
        : JSON.stringify(v) === JSON.stringify(before[k]);
      if (!same) out[k] = v;
    }
    return out;
  }

  const recapture = !!pilot && (() => {
    const b = body();
    return "success_criteria" in b || "baseline_start" in b || "baseline_end" in b;
  })();

  async function save() {
    setBusy(true); setErr(null);
    try {
      const b = body();
      const p = pilot
        ? (Object.keys(b).length ? await api.patch<Pilot>(trustApi(slug, `/pilots/${pilot.id}`), b) : pilot)
        : await api.post<Pilot>(trustApi(slug, "/pilots"), b);
      toast.success(pilot ? "Pilot updated" : "Pilot created", !pilot && bStart ? "Baseline captured from the chosen window." : undefined);
      onSaved(p);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const setRow = (i: number, patch: Partial<Row>) => setRows(rs => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const metricOpts = (metrics.data ?? []).map(m => ({ value: m.key, label: m.label }));

  // ─── Render ───────────────────────────────────────────────────────────

  return (
    <Modal
      open
      size="lg"
      onClose={() => { if (!busy) onClose(); }}
      title={pilot ? `Edit ${pilot.name}` : "New pilot"}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!valid} onClick={save}>{pilot ? "Save changes" : "Create pilot"}</Button>
        </>
      }
    >
      <div className="cx-stack" style={{ gap: 14 }}>
        <div className="trs-filters">
          <Field label="Name" htmlFor="pl-name" className="trs-filters__wide" required>
            <Input id="pl-name" value={name} maxLength={120} onChange={e => setName(e.target.value)} />
          </Field>
          <Field label="Starts" htmlFor="pl-start" required>
            <Input id="pl-start" type="date" value={start} onChange={e => setStart(e.target.value)} />
          </Field>
          <Field label="Ends" htmlFor="pl-end" required error={start && end && !datesOk ? "Ends before it starts." : undefined}>
            <Input id="pl-end" type="date" value={end} min={start || undefined} onChange={e => setEnd(e.target.value)} />
          </Field>
        </div>

        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="trs-section-title" style={{ marginTop: 0 }}>Modules in the pilot</legend>
          <div className="cx-row" style={{ gap: 12, flexWrap: "wrap" }}>
            {PILOT_MODULES.map(m => (
              <label key={m} className="cx-row" style={{ gap: 6 }}>
                <input type="checkbox" checked={modules.includes(m)}
                       onChange={e => setModules(ms => (e.target.checked ? [...ms, m] : ms.filter(x => x !== m)))} />
                {moduleLabel(m)}
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <div className="trs-section-title" style={{ marginTop: 0 }}>Success criteria</div>
          <div className="cx-stack" style={{ gap: 10 }}>
            {rows.map((r, i) => (
              <div key={i} className="trs-diff__row">
                <div className="trs-filters">
                  <Field label="Metric" htmlFor={`pl-m-${i}`}>
                    <Select id={`pl-m-${i}`} value={r.metric_key} placeholder={metrics.loading ? "Loading metrics" : "Pick a metric"}
                            options={metricOpts} onChange={e => setRow(i, { metric_key: e.target.value })} />
                  </Field>
                  <Field label="Must be" htmlFor={`pl-c-${i}`}>
                    <Select id={`pl-c-${i}`} value={r.comparator} onChange={e => setRow(i, { comparator: e.target.value as Comparator })}
                            options={COMPARATORS.map(c => ({ value: c, label: COMPARATOR_LABEL[c] }))} />
                  </Field>
                  <Field label={r.comparator.endsWith("_pct") ? "Target (%)" : "Target"} htmlFor={`pl-t-${i}`}>
                    <Input id={`pl-t-${i}`} inputMode="decimal" value={r.target} invalid={!!r.target && !isNum(r.target)}
                           onChange={e => setRow(i, { target: e.target.value })} />
                  </Field>
                  <Field label="Weight" htmlFor={`pl-w-${i}`}>
                    <Input id={`pl-w-${i}`} inputMode="decimal" value={r.weight} invalid={!isNum(r.weight) || Number(r.weight) <= 0}
                           onChange={e => setRow(i, { weight: e.target.value })} />
                  </Field>
                  <Field label="Label (optional)" htmlFor={`pl-l-${i}`} className="trs-filters__wide">
                    <Input id={`pl-l-${i}`} value={r.label} maxLength={200} placeholder="How the owner will read it"
                           onChange={e => setRow(i, { label: e.target.value })} />
                  </Field>
                </div>
                {rows.length > 1 && (
                  <Button size="sm" variant="ghost" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}>Remove</Button>
                )}
              </div>
            ))}
            {rows.length < 20 && (
              <div><Button size="sm" variant="secondary" onClick={() => setRows(rs => [...rs, blankRow()])}>Add criterion</Button></div>
            )}
          </div>
        </div>

        <div className="trs-filters">
          <Field label="Baseline from" htmlFor="pl-bs" hint="Needed for improve or reduce by % criteria. Must end before the pilot starts.">
            <Input id="pl-bs" type="date" value={bStart} onChange={e => setBStart(e.target.value)} />
          </Field>
          <Field label="Baseline to" htmlFor="pl-be" error={!baselineOk ? "Set both dates, ending before the pilot starts." : undefined}>
            <Input id="pl-be" type="date" value={bEnd} onChange={e => setBEnd(e.target.value)} />
          </Field>
          <Field label="On track at" htmlFor="pl-thr" hint="Share of weighted criteria met (%)">
            <Input id="pl-thr" inputMode="decimal" value={threshold} invalid={!thrOk} onChange={e => setThreshold(e.target.value)} />
          </Field>
          <Field label={`Price after the pilot (${fmt.currency})`} htmlFor="pl-price" error={!priceOk ? "Enter an amount." : undefined}>
            <Input id="pl-price" inputMode="decimal" value={price} placeholder="Optional" onChange={e => setPrice(e.target.value)} />
          </Field>
          <Field label="Billed" htmlFor="pl-period">
            <Select id="pl-period" value={period} onChange={e => setPeriod(e.target.value as "month" | "year")}
                    options={[{ value: "month", label: "Monthly" }, { value: "year", label: "Yearly" }]} />
          </Field>
        </div>

        {!bStart && rows.some(r => r.comparator.endsWith("_pct")) && (
          <p className="trs-small" role="note" style={{ margin: 0, color: "var(--cx-warn)" }}>
            A % change criterion needs a baseline window, otherwise it scores as no data.
          </p>
        )}
        {recapture && (
          <p className="trs-small" role="note" style={{ margin: 0, color: "var(--cx-warn)" }}>
            Changing criteria or the baseline window re-captures the baseline when you save.
          </p>
        )}
        {err && <div role="alert" style={{ color: "var(--cx-bad)" }}>{err}</div>}
      </div>
    </Modal>
  );
}
