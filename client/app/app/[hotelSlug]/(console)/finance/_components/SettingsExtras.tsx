"use client";

/* The settings page's other cards: the pilot baseline (registered once,
   immutable), the statistical inputs a month's checks read (available
   rooms, PMS rooms revenue, OTA share and expected commission; manual
   overrides are audited and re-run that month's checks), and the public
   market benchmarks behind the GOPPAR chip (Clefs admins enter them). */

import { useState } from "react";
import { LuExternalLink } from "react-icons/lu";
import { apiFetch, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Field, Input, MoneyText, Select, Skeleton, useToast } from "../../../../../ui";
import { addMonths, finApi, majorToMinor, monthKey, monthLabel, num, pctText } from "../lib";
import type { Baseline, StatInputs } from "../types";
import { errText } from "./bits";

// ─── Baseline ────────────────────────────────────────────────────────────

export function BaselineCard({ slug, canRegister }: { slug: string; canRegister: boolean }) {
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<{ baseline: Baseline | null }>(finApi(slug, "/baseline"), [slug]);
  const [busy, setBusy] = useState(false);
  const b = res.data?.baseline;

  const register = async () => {
    if (!window.confirm("Register the baseline now? It stores up to 12 trailing published months with today's mapping, and can never be changed.")) return;
    setBusy(true);
    try {
      await apiFetch(finApi(slug, "/baseline"), { method: "POST", json: {} });
      toast.success("Baseline registered");
      res.reload();
    } catch (err) {
      toast.error("Could not register the baseline", errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Pilot baseline" subtitle="The before-picture: trailing published months frozen with their mapping, so later months compare like with like.">
      {res.loading && !res.data ? <Skeleton lines={2} /> : b ? (
        <dl className="fin-kv">
          <dt>Months</dt><dd>{monthLabel(b.period_start, true)} to {monthLabel(b.period_end, true)}</dd>
          <dt>Registered</dt><dd>{fmt.date(b.registered_at, "medium")}</dd>
          <dt>Status</dt><dd><Badge tone="ok" dot>Locked</Badge></dd>
        </dl>
      ) : canRegister ? (
        <div className="fin-form">
          <div className="fin-muted">No baseline yet. Register it once the trailing months are published and their mapping confirmed.</div>
          <div><Button loading={busy} onClick={register}>Register baseline</Button></div>
        </div>
      ) : (
        <div className="fin-muted">No baseline yet. The owner or finance role registers it.</div>
      )}
    </Card>
  );
}

// ─── Statistical inputs ──────────────────────────────────────────────────

const INPUTS: { key: keyof StatInputs; label: string; kind: "int" | "money" | "share" }[] = [
  { key: "available_rn", label: "Available room nights", kind: "int" },
  { key: "rooms_sold", label: "Rooms sold", kind: "int" },
  { key: "rooms_rev_pms_minor", label: "PMS rooms revenue", kind: "money" },
  { key: "ota_share", label: "OTA share of room nights (%)", kind: "share" },
  { key: "ota_commission_expected_minor", label: "Expected OTA commission", kind: "money" },
];

export function StatInputsCard({ slug, canManage }: { slug: string; canManage: boolean }) {
  const fmt = useFormat();
  const toast = useToast();
  const [month, setMonth] = useState(() => addMonths(monthKey(fmt.dayKey(new Date())), -1));
  const res = useApi<StatInputs>(/^\d{4}-\d{2}$/.test(month) ? finApi(slug, `/stat-inputs/${month}`) : null, [slug, month]);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const d = res.data;
  const ccy = d?.currency;

  const show = (k: keyof StatInputs, kind: string) => {
    const v = d?.[k] as number | string | null | undefined;
    if (v === null || v === undefined) return "n/a";
    if (kind === "money") return <MoneyText amount={v as number} minor currency={ccy} />;
    if (kind === "share") return pctText((num(v as string) ?? 0) * 100);
    return Number(v).toLocaleString("en");
  };

  const save = async () => {
    const body: Record<string, number> = {};
    for (const f of INPUTS) {
      const raw = edit[f.key]?.trim();
      if (!raw) continue;
      const v = f.kind === "money" ? majorToMinor(raw) : f.kind === "share" ? Number(raw) / 100 : Number(raw);
      if (v === null || !Number.isFinite(v) || v < 0) {
        toast.error("Check the numbers", `${f.label} is not a valid amount.`);
        return;
      }
      body[f.key] = f.kind === "int" ? Math.round(v) : v;
    }
    if (!Object.keys(body).length) return;
    setBusy(true);
    try {
      await apiFetch(finApi(slug, `/stat-inputs/${month}`), { method: "PUT", json: body });
      toast.success("Inputs saved", `${monthLabel(month)} checks were re-run.`);
      setEdit({});
      res.reload();
    } catch (err) {
      toast.error("Could not save the inputs", errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Month inputs" subtitle="What the checks compare the P&L against. Normally read from the PMS; override when the PMS is wrong.">
      <div className="fin-form">
        <Field label="Month">
          <Input type="month" value={month} onChange={e => { setMonth(e.target.value); setEdit({}); }} aria-label="Inputs month" />
        </Field>
        {res.loading && !d ? <Skeleton lines={3} /> : res.error ? (
          <div className="fin-notice fin-notice--bad">{errText(res.error)}</div>
        ) : d ? (
          <>
            <div className="fin-small">
              {d.source === "stored" ? "Stored for this month" : "Computed live from the PMS"}
              {d.overridden?.length ? `; overridden: ${d.overridden.length}` : ""}
            </div>
            <div className="fin-form-grid">
              {INPUTS.map(f => (
                <Field key={f.key} label={f.label} hint={<>Now {show(f.key, f.kind)}{d.overridden?.includes(f.key) ? " (override)" : ""}</>}>
                  <Input inputMode="decimal" value={edit[f.key] ?? ""} placeholder="Keep" disabled={!canManage}
                    onChange={e => setEdit(p => ({ ...p, [f.key]: e.target.value }))} />
                </Field>
              ))}
            </div>
            {canManage && (
              <div><Button loading={busy} disabled={!Object.values(edit).some(v => v.trim())} onClick={save}>Save overrides</Button></div>
            )}
          </>
        ) : null}
      </div>
    </Card>
  );
}

// ─── Market benchmarks (GOPPAR chip) ─────────────────────────────────────

interface Benchmark {
  id: number;
  region: string;
  metric: string;
  period: string;
  period_kind: "month" | "ytd";
  value: string | number;
  source_url: string;
  publisher: string | null;
  entered_at: string;
}

export function BenchmarksCard({ slug }: { slug: string }) {
  const { isAdmin } = useSession();
  const res = useApi<{ benchmarks: Benchmark[]; caveat: string }>(finApi(slug, "/benchmarks"), [slug]);
  const rows = res.data?.benchmarks ?? [];
  return (
    <Card title="Market benchmarks" subtitle={res.data?.caveat ?? "Public market GOPPAR figures; context only, never a Clefs outcome."}>
      {res.loading && !res.data ? <Skeleton lines={3} /> : res.error ? (
        <div className="fin-notice fin-notice--bad">{errText(res.error)}</div>
      ) : rows.length === 0 ? (
        <div className="fin-muted">No public benchmark entered yet, so the GOPPAR chip stays hidden.</div>
      ) : (
        <ul className="fin-list">
          {rows.slice(0, 12).map(b => (
            <li key={b.id} className="fin-item">
              <div className="fin-item__head">
                <span className="fin-item__title">{b.region}, {b.period_kind === "ytd" ? `YTD ${b.period}` : monthLabel(b.period)}</span>
                <span className="fin-num">GOPPAR {pctText(num(String(b.value)), 1, true)} year on year</span>
              </div>
              <a className="fin-link fin-small" href={b.source_url} target="_blank" rel="noopener noreferrer">
                {b.publisher || "Source"} <LuExternalLink aria-hidden="true" size={11} />
              </a>
            </li>
          ))}
        </ul>
      )}
      {isAdmin && <BenchmarkForm slug={slug} onSaved={res.reload} />}
    </Card>
  );
}

function BenchmarkForm({ slug, onSaved }: { slug: string; onSaved: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ region: "", period: "", period_kind: "month", value: "", source_url: "", publisher: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF(p => ({ ...p, [k]: e.target.value }));
  const ok = f.region.trim() && /^\d{4}-\d{2}$/.test(f.period) && Number.isFinite(Number(f.value)) && f.value.trim()
    && /^https?:\/\//.test(f.source_url.trim());

  const save = async () => {
    setBusy(true);
    try {
      await apiFetch(finApi(slug, "/benchmarks"), {
        method: "POST",
        json: { region: f.region.trim(), metric: "goppar_yoy", period: f.period, period_kind: f.period_kind,
          value: f.value.trim(), source_url: f.source_url.trim(), publisher: f.publisher.trim() || undefined },
      });
      toast.success("Benchmark added");
      setF({ region: "", period: "", period_kind: "month", value: "", source_url: "", publisher: "" });
      onSaved();
    } catch (err) {
      toast.error("Could not add the benchmark", errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fin-form" style={{ marginTop: 16 }}>
      <div className="fin-small"><Badge tone="info">Clefs admin</Badge> Add a published figure. It applies to every hotel in the region.</div>
      <div className="fin-form-grid">
        <Field label="Region"><Input value={f.region} onChange={set("region")} placeholder="Dubai" maxLength={60} /></Field>
        <Field label="Month"><Input type="month" value={f.period} onChange={set("period")} /></Field>
        <Field label="Period">
          <Select value={f.period_kind} onChange={set("period_kind")}>
            <option value="month">That month</option>
            <option value="ytd">Year to date</option>
          </Select>
        </Field>
        <Field label="GOPPAR change (%)"><Input inputMode="decimal" value={f.value} onChange={set("value")} placeholder="-47.8" /></Field>
        <Field label="Source link"><Input type="url" value={f.source_url} onChange={set("source_url")} placeholder="https://" /></Field>
        <Field label="Publisher"><Input value={f.publisher} onChange={set("publisher")} placeholder="HotStats" maxLength={120} /></Field>
      </div>
      <div><Button size="sm" loading={busy} disabled={!ok} onClick={save}>Add benchmark</Button></div>
    </div>
  );
}
