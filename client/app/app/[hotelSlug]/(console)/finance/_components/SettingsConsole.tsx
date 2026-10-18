"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Finance settings (04 spec 6.8 settings/): the ratio bands and anomaly
   thresholds (finance.manage), the service-charge treatment (Profit
   Guard's policy is its single source, 6.4; written there with
   profit.manage since Profit Guard has no settings page), the pilot
   baseline, the month's statistical inputs, and the market benchmarks
   behind the GOPPAR chip.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { apiFetch, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Segmented, Skeleton, useToast } from "../../../../../ui";
import { finApi } from "../lib";
import type { FinanceSettings, SettingsOut } from "../types";
import { errText } from "./bits";
import { BaselineCard, BenchmarksCard, StatInputsCard } from "./SettingsExtras";

type NumKey = Exclude<keyof FinanceSettings, "market_region">;

const FIELDS: { key: NumKey; label: string; hint: string; step: number }[] = [
  { key: "labour_band_min_pct", label: "Labour band, low (%)", hint: "All payroll as a share of revenue.", step: 0.5 },
  { key: "labour_band_max_pct", label: "Labour band, high (%)", hint: "Above this, the month is flagged.", step: 0.5 },
  { key: "eww_band_min_pct", label: "Energy, water and waste, low (%)", hint: "", step: 0.5 },
  { key: "eww_band_max_pct", label: "Energy, water and waste, high (%)", hint: "", step: 0.5 },
  { key: "zscore_min", label: "Unusual move (z-score)", hint: "How far from the usual months a figure must be.", step: 0.1 },
  { key: "delta_min_pp", label: "Smallest move (points)", hint: "And by at least this many percentage points.", step: 0.5 },
  { key: "trailing_months", label: "Months compared", hint: "The trailing published months used as usual.", step: 1 },
];

export default function SettingsConsole() {
  const { slug } = useHotel();
  const { can } = useSession();
  if (!can("finance.pl.view")) {
    return <Card><EmptyState title="No access to finance settings" body="The owner, GM and finance roles see these." /></Card>;
  }
  return (
    <>
      <BandsCard slug={slug} canManage={can("finance.manage")} />
      <div className="fin-grid2">
        <BaselineCard slug={slug} canRegister={can("finance.publish")} />
        <StatInputsCard slug={slug} canManage={can("finance.manage")} />
      </div>
      <BenchmarksCard slug={slug} />
    </>
  );
}

// ─── Service charge (written to Profit Guard's policy) ───

// WHY this card writes at all: Profit Guard has no settings page, so a link
// to one 404'd and the treatment (it moves NRevPAR and the USALI rooms line)
// could be changed nowhere. The write goes to Profit Guard's own policies
// route (sc.edit_api, profit.manage), so Finance still keeps no copy.
function ScTreatment({ slug, sc, onSaved }: {
  slug: string;
  sc: SettingsOut["service_charge"];
  onSaved: () => void;
}) {
  const toast = useToast();
  const [pick, setPick] = useState<"revenue" | "pass">(sc.treat_sc_as_revenue ? "revenue" : "pass");
  const [busy, setBusy] = useState(false);
  const dirty = (pick === "revenue") !== sc.treat_sc_as_revenue;

  const save = async () => {
    setBusy(true);
    try {
      await apiFetch(`/hotels/${encodeURIComponent(slug)}/${sc.edit_api}`, {
        method: "PUT", json: { treat_sc_as_revenue: pick === "revenue" },
      });
      toast.success("Service charge treatment saved", "The weekly pulse and the next P&L mapping follow it.");
      onSaved();
    } catch (err) {
      toast.error("Could not save", errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fin-row">
      <span>
        Treated as{" "}
        {sc.treat_sc_as_revenue ? <Badge tone="info">Hotel revenue</Badge> : <Badge>Staff pass-through</Badge>}
        <span className="fin-small"> Confirm this with your accountant.</span>
      </span>
      {sc.can_edit ? (
        <span className="fin-row">
          <Segmented size="sm" ariaLabel="Service charge treatment" value={pick} onChange={setPick}
            options={[{ value: "revenue", label: "Hotel revenue" }, { value: "pass", label: "Staff pass-through" }]} />
          {dirty && <Button size="sm" variant="primary" loading={busy} onClick={save}>Save</Button>}
        </span>
      ) : sc.settings_path ? (
        <a className="fin-link" href={`/app/${encodeURIComponent(slug)}/${sc.settings_path}`}>Change in Profit Guard</a>
      ) : (
        <span className="fin-small">An owner, GM or finance lead with Profit Guard access can change it.</span>
      )}
    </div>
  );
}

function BandsCard({ slug, canManage }: { slug: string; canManage: boolean }) {
  const res = useApi<SettingsOut>(finApi(slug, "/settings"), [slug]);
  const sc = res.data?.service_charge;
  const settings = res.data?.settings;
  return (
    <>
      <Card title="Service charge" subtitle="One source of truth: Profit Guard's policy. Finance follows it in the weekly pulse and the P&L mapping.">
        {res.loading && !res.data ? <Skeleton lines={2} /> : sc ? (
          // Keyed on the stored value: a save remounts the card with it.
          <ScTreatment key={String(sc.treat_sc_as_revenue)} slug={slug} sc={sc} onSaved={res.reload} />
        ) : null}
      </Card>
      <Card title="Ratio bands and anomaly thresholds" subtitle="A month is flagged when a ratio leaves its band, or moves unusually against the months before it.">
        {res.loading && !res.data ? <Skeleton lines={4} /> : res.error ? (
          <EmptyState title="Could not load settings" body={errText(res.error)} />
        ) : settings ? (
          // Keyed on the saved values: a save remounts the form with them,
          // so no effect has to copy server data into local state.
          <BandsForm key={JSON.stringify(settings)} slug={slug} initial={settings} canManage={canManage} onSaved={res.reload} />
        ) : null}
      </Card>
    </>
  );
}

function BandsForm({ slug, initial, canManage, onSaved }: {
  slug: string;
  initial: FinanceSettings;
  canManage: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(initial).map(([k, v]) => [k, v === null ? "" : String(v)])));
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const body: Record<string, number | string | null> = {};
    for (const f of FIELDS) body[f.key] = Number(form[f.key]);
    body.market_region = form.market_region?.trim() || null;
    setBusy(true);
    try {
      await apiFetch(finApi(slug, "/settings"), { method: "PUT", json: { settings: body } });
      toast.success("Finance settings saved", "The next anomaly scan uses them.");
      onSaved();
    } catch (err) {
      toast.error("Could not save", errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fin-form">
      <div className="fin-form-grid">
        {FIELDS.map(f => (
          <Field key={f.key} label={f.label} hint={f.hint || undefined}>
            <Input type="number" inputMode="decimal" step={f.step} value={form[f.key] ?? ""} disabled={!canManage}
              onChange={e => setForm(p => ({ ...p, [f.key]: e.target.value }))} />
          </Field>
        ))}
        <Field label="Market region" hint="Which public GOPPAR figures the chip compares against, e.g. Dubai.">
          <Input value={form.market_region ?? ""} maxLength={60} disabled={!canManage}
            onChange={e => setForm(p => ({ ...p, market_region: e.target.value }))} />
        </Field>
      </div>
      {canManage ? (
        <div><Button loading={busy} onClick={save}>Save settings</Button></div>
      ) : (
        <div className="fin-small">Only the owner or finance role can change these.</div>
      )}
    </div>
  );
}
