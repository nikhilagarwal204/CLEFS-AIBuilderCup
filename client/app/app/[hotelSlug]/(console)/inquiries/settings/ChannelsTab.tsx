"use client";

/* Channels (4.3): WhatsApp and the email alias are set up once for the
   hotel (core settings) and only shown here; a Meta page (lead ads) and a
   Google Ads lead form are connected here. Secrets go in and never come
   back: the page token is sealed on the server, and the Google key is
   shown exactly once, right after connecting, to paste into Google Ads. */

import { useState } from "react";
import { API_ORIGIN, api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Field, Input, Skeleton, useToast } from "../../../../../ui";
import { errorText, inqPath } from "../lib";
import { useCopy, type ChannelAccount, type ChannelsView } from "../SetupChecklist";

interface GoogleConnected extends ChannelAccount { webhook_path: string; google_key: string }

export default function ChannelsTab() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const copy = useCopy();
  const ch = useApi<ChannelsView>(inqPath(slug, "/channels"));
  const [busy, setBusy] = useState<string | null>(null);
  const [meta, setMeta] = useState({ page_id: "", page_access_token: "", page_name: "" });
  const [google, setGoogle] = useState<GoogleConnected | null>(null);

  const acct = (kind: string) => (ch.data?.accounts ?? []).find(a => a.kind === kind);
  const metaAcct = acct("meta_page");
  const googleAcct = acct("google_leadform");

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    try { await fn(); ch.reload(); } catch (err) { toast.error("Not saved", errorText(err)); } finally { setBusy(null); }
  }

  const connectMeta = () => run("meta", async () => {
    const res = await api.post<ChannelAccount>(inqPath(slug, "/channels"), {
      kind: "meta_page", page_id: meta.page_id.trim(), page_access_token: meta.page_access_token.trim(),
      page_name: meta.page_name.trim() || undefined,
    });
    setMeta({ page_id: "", page_access_token: "", page_name: "" });
    if (res.status === "subscribe_failed") {
      toast({ tone: "warn", title: "Page saved, but Meta refused the lead subscription",
        body: "Check that the token has leads_retrieval and pages_manage_metadata, then connect again." });
    } else toast.success("Meta page connected");
  });

  const connectGoogle = () => run("google", async () => {
    const res = await api.post<GoogleConnected>(inqPath(slug, "/channels"), { kind: "google_leadform" });
    setGoogle(res);
    toast.success("Google lead form connected", "Copy the webhook address and key into Google Ads now.");
  });

  const disconnect = (kind: string, label: string) => {
    if (!window.confirm(`Disconnect ${label}? New leads from it will stop arriving.`)) return;
    void run(`del:${kind}`, async () => {
      await api.del(inqPath(slug, `/channels/${kind}`));
      if (kind === "google_leadform") setGoogle(null);
      toast.success(`${label} disconnected`);
    });
  };

  if (ch.loading && !ch.data) return <Skeleton lines={6} height={16} />;
  if (ch.error) return <Card><p className="inq-muted">{ch.error.message}</p><Button onClick={ch.reload}>Try again</Button></Card>;
  const since = (a?: ChannelAccount) => (a?.connected_at ? `since ${fmt.date(a.connected_at, "medium")}` : "");

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="WhatsApp and email" subtitle="Set up once for the hotel in Settings; inquiries use them automatically.">
        <dl className="inq-kv">
          <dt>WhatsApp</dt>
          <dd>{ch.data?.whatsapp_connected ? <Badge tone="ok">Connected</Badge> : <Badge tone="warn">Not connected</Badge>}</dd>
          <dt>Email alias</dt>
          <dd>{ch.data?.email_address
            ? <span className="inq-row"><span className="inq-ref">{ch.data.email_address}</span>
                <Button size="sm" variant="ghost" onClick={() => copy(ch.data!.email_address!, "Address copied")}>Copy</Button></span>
            : "Not set up"}</dd>
        </dl>
        <p className="inq-muted" style={{ margin: "10px 0 0" }}>
          Forward your reservations mailbox to the alias: replies to a quote thread back into the same inquiry.
        </p>
      </Card>
      <Card title="Meta lead ads" subtitle="Leads from Facebook and Instagram lead-form ads."
        actions={metaAcct ? <Button size="sm" variant="ghost" loading={busy === "del:meta_page"} onClick={() => disconnect("meta_page", "the Meta page")}>Disconnect</Button> : undefined}>
        {metaAcct ? (
          <p style={{ margin: 0 }}>
            <Badge tone={metaAcct.status === "active" ? "ok" : "warn"}>{metaAcct.status === "active" ? "Receiving leads" : "Subscription failed"}</Badge>{" "}
            {String(metaAcct.config?.page_name ?? metaAcct.config?.page_id ?? "")} <span className="inq-muted">{since(metaAcct)}</span>
          </p>
        ) : (
          <div className="inq-form">
            <Field label="Page ID"><Input value={meta.page_id} inputMode="numeric" onChange={e => setMeta({ ...meta, page_id: e.target.value })} /></Field>
            <Field label="Page name (optional)"><Input value={meta.page_name} onChange={e => setMeta({ ...meta, page_name: e.target.value })} /></Field>
            <Field label="Page access token" className="inq-full" hint="A long-lived page token with leads_retrieval. Stored encrypted; never shown again.">
              <Input type="password" autoComplete="off" value={meta.page_access_token}
                onChange={e => setMeta({ ...meta, page_access_token: e.target.value })} />
            </Field>
            <div className="inq-full">
              <Button variant="primary" loading={busy === "meta"} disabled={!meta.page_id.trim() || !meta.page_access_token.trim()}
                onClick={connectMeta}>Connect page</Button>
            </div>
          </div>
        )}
      </Card>
      <Card title="Google Ads lead form" subtitle="Leads from lead-form assets on Google Search, YouTube and Discovery."
        actions={googleAcct ? <Button size="sm" variant="ghost" loading={busy === "del:google_leadform"} onClick={() => disconnect("google_leadform", "the Google lead form")}>Disconnect</Button> : undefined}>
        {google ? (
          <div style={{ display: "grid", gap: 10 }}>
            <p className="inq-note inq-note--warn" style={{ margin: 0 }}>Copy both now. The key is not shown again (reconnect to get a new one).</p>
            <Field label="Webhook URL"><pre className="inq-code">{API_ORIGIN + google.webhook_path}</pre></Field>
            <Button size="sm" onClick={() => copy(API_ORIGIN + google.webhook_path, "Webhook URL copied")}>Copy URL</Button>
            <Field label="Key"><pre className="inq-code">{google.google_key}</pre></Field>
            <Button size="sm" onClick={() => copy(google.google_key, "Key copied")}>Copy key</Button>
          </div>
        ) : googleAcct ? (
          <p style={{ margin: 0 }}><Badge tone="ok">Connected</Badge> <span className="inq-muted">{since(googleAcct)}</span></p>
        ) : (
          <Button variant="primary" loading={busy === "google"} onClick={connectGoogle}>Connect Google lead form</Button>
        )}
      </Card>
    </div>
  );
}
