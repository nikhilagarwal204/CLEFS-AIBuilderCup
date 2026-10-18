"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Accounting connectors (04 spec 6.3): Xero and QuickBooks Online through
   the core OAuth flow, Zoho Books as "coming next", Tally as upload-only.
   Connect fetches GET /hotels/{slug}/oauth/{provider}/start?format=json
   and navigates to the provider; the core callback comes back to this page
   with ?oauth=<provider>&oauth_status=..., which page.tsx hands to the
   toast. A connected provider syncs one month on demand; the monthly job
   does the previous month on day 3 by itself.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { apiFetch, apiUrl, hotelPath, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, EmptyState, Field, Input, Skeleton, useToast, type Tone } from "../../../../../ui";
import { finApi, finHref, monthLabel, PROVIDER_LABELS } from "../lib";
import type { Connection, SyncOut } from "../types";
import { errText } from "./bits";

interface StartOut { provider: string; authorize_url: string; expires_in: number }

const STATUS: Record<string, { label: string; tone: Tone }> = {
  active: { label: "Connected", tone: "ok" },
  not_connected: { label: "Not connected", tone: "neutral" },
  disconnected: { label: "Disconnected", tone: "neutral" },
  revoked: { label: "Access revoked", tone: "bad" },
  expired: { label: "Needs reconnecting", tone: "warn" },
};

export default function Connectors({ slug, canConnect, onSynced }: {
  slug: string;
  canConnect: boolean;
  onSynced?: () => void;
}) {
  const res = useApi<{ connections: Connection[] }>(finApi(slug, "/connections"), [slug]);
  const rows = res.data?.connections ?? [];
  return (
    <Card title="Accounting connections" subtitle="Connect once and the previous month's P&L arrives on the 3rd of every month.">
      {res.loading ? (
        <Skeleton lines={4} />
      ) : res.error ? (
        <EmptyState title="Could not load connections" body={errText(res.error)} />
      ) : (
        <div className="fin-grid2">
          {rows.map(c => c.upload_only
            ? <TallyCard key={c.provider} />
            : <ProviderCard key={c.provider} slug={slug} c={c} canConnect={canConnect}
                onChanged={() => { res.reload(); onSynced?.(); }} />)}
        </div>
      )}
    </Card>
  );
}

function TallyCard() {
  return (
    <div className="fin-conn">
      <div className="fin-row">
        <span className="fin-conn__name">Tally</span>
        <Badge>Upload only</Badge>
      </div>
      <div className="fin-muted">
        Tally has no online connection. Export the Profit and Loss to Excel from Tally and upload it here; its groups
        (Direct and Indirect Incomes and Expenses, Duties and Taxes) are recognised automatically.
      </div>
    </div>
  );
}

// ─── One provider ────────────────────────────────────────────────────────

function ProviderCard({ slug, c, canConnect, onChanged }: {
  slug: string;
  c: Connection;
  canConnect: boolean;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const [busy, setBusy] = useState<"connect" | "sync" | "disconnect" | null>(null);
  const [month, setMonth] = useState("");
  const name = PROVIDER_LABELS[c.provider] ?? c.label;
  const st = STATUS[c.status ?? "not_connected"] ?? { label: c.status ?? "Unknown", tone: "neutral" as Tone };
  const connected = c.status === "active";
  const next = finHref(slug, "/imports");
  // The plain 302 link: works with scripts blocked and is what "open in a
  // new tab" follows. The button below prefers the JSON form for errors.
  const startHref = apiUrl(withQuery(hotelPath(slug, `/oauth/${c.provider}/start`), { next }));

  const connect = async () => {
    setBusy("connect");
    try {
      const out = await apiFetch<StartOut>(hotelPath(slug, `/oauth/${c.provider}/start`), { query: { format: "json", next } });
      window.location.assign(out.authorize_url);
    } catch (err) {
      toast.error(`Could not start the ${name} connection`, errText(err));
      setBusy(null);
    }
  };

  const sync = async () => {
    setBusy("sync");
    try {
      const out = await apiFetch<SyncOut>(finApi(slug, `/connections/${c.provider}/sync`), {
        method: "POST", json: { month: month || undefined, statement_kind: "pl" },
      });
      toast.success(
        out.reused ? `${monthLabel(out.month)} unchanged` : `${monthLabel(out.month)} synced`,
        out.reused ? `${name} returned the same report as last time.` : `${out.lines} lines from ${name}. Review the mapping next.`,
      );
      onChanged();
    } catch (err) {
      toast.error(`${name} sync failed`, errText(err));
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    if (!window.confirm(`Disconnect ${name}? Months already imported stay; new months stop arriving.`)) return;
    setBusy("disconnect");
    try {
      await apiFetch(hotelPath(slug, `/oauth/${c.provider}`), { method: "DELETE" });
      toast.success(`${name} disconnected`);
      onChanged();
    } catch (err) {
      toast.error("Could not disconnect", errText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fin-conn">
      <div className="fin-row">
        <span className="fin-conn__name">{name}</span>
        {c.coming_next ? <Badge tone="gold">Coming next</Badge> : <Badge tone={st.tone} dot>{st.label}</Badge>}
      </div>
      {c.account_ref && <div className="fin-small">Organisation {c.account_ref}</div>}
      {c.last_sync_at && (
        <div className="fin-small">Last sync {fmt.relative(c.last_sync_at)}{c.sync_status ? ` (${c.sync_status})` : ""}</div>
      )}
      {c.last_error && <div className="fin-small" style={{ color: "var(--clefs-gold-300)" }}>Last error: {c.last_error}</div>}
      {c.coming_next ? (
        <div className="fin-muted">Zoho Books has no report API we can rely on yet. Upload its P&L export for now.</div>
      ) : !c.configured ? (
        <div className="fin-muted">This connection needs Clefs app keys on the server. Ask Clefs support to switch it on.</div>
      ) : !canConnect ? (
        <div className="fin-muted">Only the owner or finance role can connect or sync accounting systems.</div>
      ) : connected ? (
        <div className="fin-toolbar">
          <Field label="Month to sync" hint="Blank means last month.">
            <Input type="month" value={month} onChange={e => setMonth(e.target.value)} aria-label={`${name} month to sync`} />
          </Field>
          <Button size="sm" loading={busy === "sync"} disabled={!!busy} onClick={sync}>Sync now</Button>
          <Button size="sm" variant="ghost" loading={busy === "disconnect"} disabled={!!busy} onClick={disconnect}>Disconnect</Button>
        </div>
      ) : (
        <div className="fin-toolbar">
          <Button size="sm" loading={busy === "connect"} disabled={!!busy} onClick={connect}>Connect {name}</Button>
          <a className="fin-link fin-small" href={startHref}>or open the sign-in link</a>
        </div>
      )}
    </div>
  );
}
