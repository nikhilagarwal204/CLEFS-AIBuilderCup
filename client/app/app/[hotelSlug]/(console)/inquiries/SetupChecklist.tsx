"use client";

/* Empty state (4.11): no inquiries yet. A manager sees what is connected
   (WhatsApp, the email alias, a Meta page, a Google lead form, a payment
   provider) and the website widget's embed code, because an empty inbox
   almost always means "nothing is feeding it yet". Everyone else gets a
   plain empty state (the channel routes need inquiries.manage). */

import { useState } from "react";
import { useApi } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { Button, Card, EmptyState, Skeleton, useToast } from "../../../../ui";
import { checklistState, inqPath } from "./lib";

export interface ChannelAccount {
  id: number;
  kind: "web_widget" | "meta_page" | "google_leadform" | string;
  config: Record<string, unknown> | null;
  status: string;
  connected_by: number | null;
  connected_at: string | null;
}

export interface ChannelsView {
  accounts: ChannelAccount[];
  whatsapp_connected: boolean;
  email_address: string | null;
}

export interface WidgetView {
  widget_key: string;
  embed_code: string;
  iframe_path: string;
  allowed_origins: string[];
}

export interface PaymentAccount {
  id: number;
  kind: string;
  provider: string;
  config: Record<string, unknown> | null;
  status: string;
  connected_by: number | null;
  connected_at: string | null;
}

/** Copy to the clipboard with a toast; falls back to "select it yourself"
    where the Clipboard API is blocked (http, an old browser, an iframe). */
export function useCopy() {
  const toast = useToast();
  return async (text: string, what = "Copied") => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(what);
    } catch {
      toast({ tone: "warn", title: "Copy is blocked here. Select the text and copy it." });
    }
  };
}

export default function SetupChecklist({ canManage }: { canManage: boolean }) {
  const { slug } = useHotel();
  const channels = useApi<ChannelsView>(canManage ? inqPath(slug, "/channels") : null);
  const payments = useApi<PaymentAccount[]>(canManage ? inqPath(slug, "/payment-accounts") : null);
  const [showCode, setShowCode] = useState(false);
  const widget = useApi<WidgetView>(canManage && showCode ? inqPath(slug, "/widget") : null);
  const copy = useCopy();

  if (!canManage) {
    return (
      <Card>
        <EmptyState title="No inquiries yet"
          body="New booking requests from WhatsApp, email, the website form and lead ads will appear here." />
      </Card>
    );
  }

  const accounts = channels.data?.accounts ?? [];
  const has = (kind: string) => accounts.some(a => a.kind === kind && a.status !== "disconnected");
  const items: { key: string; label: string; done: boolean; hint: string }[] = [
    { key: "wa", label: "WhatsApp", done: !!channels.data?.whatsapp_connected,
      hint: "Messages from people who are not in-house guests become inquiries, including click-to-WhatsApp ads." },
    { key: "email", label: "Email alias", done: !!channels.data?.email_address,
      hint: channels.data?.email_address
        ? `Forward your reservations inbox to ${channels.data.email_address}.`
        : "Set up the hotel's inbound email in Settings first." },
    { key: "meta", label: "Meta lead ads", done: has("meta_page"), hint: "Connect a Facebook page to receive lead-form ads." },
    { key: "google", label: "Google lead form", done: has("google_leadform"), hint: "Paste the webhook into your Google Ads lead form." },
    { key: "pay", label: "Payment provider", done: (payments.data ?? []).length > 0,
      hint: "Needed for deposit links on accepted quotes (N-Genius, Telr, Stripe or Tap)." },
  ];

  // Loading or failed: never show "Not connected" (see checklistState).
  const state = checklistState(channels, payments);
  const pending = state === "loading";
  const failed = state === "error";
  const retry = () => { channels.reload(); payments.reload(); };

  return (
    <Card title="Start receiving inquiries"
      subtitle={pending || failed ? "Nothing has arrived yet." : "Nothing has arrived yet. Connect at least one channel."}>
      {pending ? (
        <div aria-busy="true" aria-label="Checking connected channels"><Skeleton lines={5} height={18} gap={12} /></div>
      ) : failed ? (
        <div className="inq-section" role="alert">
          <p className="inq-muted" style={{ margin: 0 }}>
            Could not check which channels are connected. Nothing has been changed.
          </p>
          <div><Button size="sm" onClick={retry}>Try again</Button></div>
        </div>
      ) : (
        <ul className="inq-checklist">
          {items.map(it => (
            <li key={it.key}>
              <span className={`inq-checklist__mark${it.done ? " inq-checklist__mark--done" : ""}`} aria-hidden="true">
                {it.done ? "✓" : ""}
              </span>
              <span>
                <strong>{it.label}</strong> <span className="inq-muted">{it.done ? "Connected" : "Not connected"}</span>
                <span className="inq-sub">{it.hint}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="inq-section">
        <h3>Website form</h3>
        <p className="inq-muted" style={{ margin: 0 }}>
          Paste this code where the form should appear on your website. It asks for both required consents and
          sends the request straight here.
        </p>
        {!showCode ? (
          <div><Button onClick={() => setShowCode(true)}>Show embed code</Button></div>
        ) : widget.data ? (
          <>
            <pre className="inq-code">{widget.data.embed_code}</pre>
            <div className="inq-row">
              <Button size="sm" onClick={() => copy(widget.data!.embed_code, "Embed code copied")}>Copy code</Button>
            </div>
          </>
        ) : widget.error ? <p className="inq-muted">{widget.error.message}</p> : <p className="inq-muted">Loading...</p>}
      </div>
      <div className="inq-row" style={{ marginTop: 14 }}>
        <Button variant="primary" href={`/app/${slug}/inquiries/settings`}>Open inquiry settings</Button>
      </div>
    </Card>
  );
}
