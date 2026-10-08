"use client";

/* Payment providers (4.8): connect N-Genius, Telr, Stripe or Tap so an
   accepted quote gets a deposit link. Secrets go in once and never come
   back; the webhook address is shown once after connecting (reconnecting
   rotates it). Fees: the provider's reported fee wins; these settings are
   the fallback used to net the fee out of "commission avoided". */

import { useState } from "react";
import { API_ORIGIN, api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Field, Input, Select, Skeleton, useToast } from "../../../../../ui";
import { errorText, inqPath, majorToMinor } from "../lib";
import { useCopy, type PaymentAccount } from "../SetupChecklist";

type Provider = "ngenius" | "telr" | "stripe" | "tap";
interface FieldSpec { key: string; label: string; required?: boolean; secret?: boolean; hint?: string }

const PROVIDERS: Record<Provider, { label: string; config: FieldSpec[]; secret: FieldSpec[]; testMode?: boolean }> = {
  ngenius: {
    label: "N-Genius (Network International)",
    config: [{ key: "outlet_ref", label: "Outlet reference", required: true },
      { key: "api_base", label: "API base (optional)", hint: "Only if Network gave you a different region endpoint (https://...ngenius-payments.com)" }],
    secret: [{ key: "api_key", label: "API key", required: true, secret: true },
      { key: "webhook_secret", label: "Webhook secret", required: true, secret: true, hint: "The value you set as the webhook header in the N-Genius portal" }],
  },
  telr: {
    label: "Telr",
    config: [{ key: "store_id", label: "Store ID", required: true }],
    secret: [{ key: "auth_key", label: "Authentication key", required: true, secret: true },
      { key: "advice_secret", label: "Transaction advice secret (optional)", secret: true }],
    testMode: true,
  },
  stripe: {
    label: "Stripe",
    config: [],
    secret: [{ key: "secret_key", label: "Secret key", required: true, secret: true },
      { key: "webhook_secret", label: "Webhook signing secret", required: true, secret: true }],
  },
  tap: {
    label: "Tap Payments",
    config: [],
    secret: [{ key: "secret_key", label: "Secret key", required: true, secret: true },
      { key: "webhook_secret", label: "Webhook secret (optional)", secret: true }],
  },
};

interface Connected extends PaymentAccount { webhook_key?: string; webhook_path?: string }

export default function PaymentsTab() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const copy = useCopy();
  const accounts = useApi<PaymentAccount[]>(inqPath(slug, "/payment-accounts"));
  const [provider, setProvider] = useState<Provider>("ngenius");
  const [values, setValues] = useState<Record<string, string>>({});
  const [testMode, setTestMode] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [connected, setConnected] = useState<Connected | null>(null);
  const spec = PROVIDERS[provider];
  const feeFixedMinor = values.fee_fixed ? majorToMinor(values.fee_fixed) : null;
  const ready = [...spec.config, ...spec.secret].every(f => !f.required || values[f.key]?.trim())
    && (!values.fee_pct || /^\d{1,2}(\.\d{1,2})?$/.test(values.fee_pct)) && (!values.fee_fixed || feeFixedMinor !== null);

  async function connect() {
    setBusy("connect");
    try {
      const config: Record<string, unknown> = {};
      for (const f of spec.config) if (values[f.key]?.trim()) config[f.key] = values[f.key].trim();
      if (spec.testMode) config.test_mode = testMode;
      if (values.fee_pct) config.fee_pct = values.fee_pct;
      if (feeFixedMinor !== null) config.fee_fixed_minor = feeFixedMinor;
      const secret: Record<string, string> = {};
      for (const f of spec.secret) if (values[f.key]?.trim()) secret[f.key] = values[f.key].trim();
      const res = await api.post<Connected>(inqPath(slug, "/payment-accounts"), { provider, config, secret });
      setConnected(res);
      setValues({});
      accounts.reload();
      toast.success(`${spec.label} connected`, "Copy the webhook address into the provider's dashboard now.");
    } catch (err) {
      toast.error("Not connected", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(p: string) {
    if (!window.confirm("Disconnect this provider? Open payment links keep working at the provider, but Clefs stops tracking them.")) return;
    setBusy(`del:${p}`);
    try {
      await api.del(inqPath(slug, `/payment-accounts/${p}`));
      accounts.reload();
      toast.success("Provider disconnected");
    } catch (err) {
      toast.error("Not disconnected", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  if (accounts.loading && !accounts.data) return <Skeleton lines={5} height={16} />;
  const list = accounts.data ?? [];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {accounts.error && <p className="inq-note inq-note--bad">{accounts.error.message}</p>}
      {list.length > 0 && (
        <Card title="Connected">
          {list.map(a => (
            <div key={a.id} className="inq-row inq-row--between" style={{ padding: "6px 0" }}>
              <span className="inq-row">
                <strong>{PROVIDERS[a.provider as Provider]?.label ?? a.provider}</strong>
                <Badge tone={a.status === "active" ? "ok" : "warn"}>{a.status}</Badge>
                {a.config?.test_mode ? <Badge tone="info">Test mode</Badge> : null}
                <span className="inq-muted">{a.connected_at ? `since ${fmt.date(a.connected_at, "medium")}` : ""}</span>
              </span>
              <Button size="sm" variant="ghost" loading={busy === `del:${a.provider}`} onClick={() => disconnect(a.provider)}>Disconnect</Button>
            </div>
          ))}
        </Card>
      )}
      {connected?.webhook_path && (
        <Card title="Webhook address (shown once)" gold>
          <pre className="inq-code">{API_ORIGIN + connected.webhook_path}</pre>
          <div className="inq-row" style={{ marginTop: 10 }}>
            <Button size="sm" variant="primary" onClick={() => copy(API_ORIGIN + connected.webhook_path!, "Webhook address copied")}>Copy</Button>
            <Button size="sm" variant="ghost" onClick={() => setConnected(null)}>Done</Button>
          </div>
        </Card>
      )}
      <Card title={list.length ? "Connect another provider" : "Connect a payment provider"}
        subtitle="Reconnecting a provider replaces its keys and its webhook address.">
        <div className="inq-form">
          <Field label="Provider" className="inq-full">
            <Select value={provider} onChange={e => { setProvider(e.target.value as Provider); setValues({}); }}>
              {(Object.keys(PROVIDERS) as Provider[]).map(p => <option key={p} value={p}>{PROVIDERS[p].label}</option>)}
            </Select>
          </Field>
          {[...spec.config, ...spec.secret].map(f => (
            <Field key={f.key} label={f.label} required={f.required} hint={f.hint}>
              <Input type={f.secret ? "password" : "text"} autoComplete="off" value={values[f.key] ?? ""}
                onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))} />
            </Field>
          ))}
          <Field label="Fee % (optional)" hint="Used when the provider does not report its fee">
            <Input inputMode="decimal" placeholder="2.5" value={values.fee_pct ?? ""} onChange={e => setValues(v => ({ ...v, fee_pct: e.target.value }))} />
          </Field>
          <Field label="Fixed fee per payment (optional)">
            <Input inputMode="decimal" placeholder="1.00" value={values.fee_fixed ?? ""} onChange={e => setValues(v => ({ ...v, fee_fixed: e.target.value }))} />
          </Field>
          {spec.testMode && (
            <label className="inq-row inq-full">
              <input type="checkbox" checked={testMode} onChange={e => setTestMode(e.target.checked)} /> Test mode (no real charges)
            </label>
          )}
          <div className="inq-full">
            <Button variant="primary" loading={busy === "connect"} disabled={!ready} onClick={connect}>Connect</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
