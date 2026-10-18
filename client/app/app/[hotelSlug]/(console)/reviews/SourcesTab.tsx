"use client";

/* Where reviews come from. Google Business Profile is the preferred Google
   path (official API, replies can be posted); Google Maps through SerpApi is
   a fallback the owner must switch on after reading the vendor disclosure;
   Tripadvisor polls its 5 newest; OTAs arrive as CSV exports or forwarded
   emails. Every source feeds the same store, so the feed never cares which
   one a review came through. */

import { useState } from "react";
import { apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, Card, EmptyState, Skeleton, useToast, type Tone } from "../../../../ui";
import GoogleCard from "./GoogleCard";
import AddSource from "./AddSource";
import ImportPanel from "./ImportPanel";
import { SOURCE_KIND_LABELS, copyText, errText } from "./lib";
import type { Source, SourcesPayload } from "./types";

const STATUS_TONE: Record<Source["status"], Tone> = {
  active: "ok", paused: "neutral", error: "bad", disconnected: "warn",
};

export default function SourcesTab({ slug, canManage, onChanged }: {
  slug: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<SourcesPayload>(hotelPath(slug, "/reviews/sources"), [slug]);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = () => { res.reload(); onChanged(); };

  const act = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try {
      await fn();
      toast.success(ok);
      refresh();
    } catch (err) {
      toast.error("That did not work", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const sync = (sourceId?: number) => act(`sync:${sourceId ?? "all"}`, () => apiFetch(
    hotelPath(slug, "/reviews/sync"), { method: "POST", json: sourceId ? { source_id: sourceId } : {} },
  ), "Sync queued. New reviews appear in a few minutes.");

  const setStatus = (s: Source, status: "active" | "paused") => act(`status:${s.id}`, () => apiFetch(
    hotelPath(slug, `/reviews/sources/${s.id}`), { method: "PATCH", json: { status } },
  ), status === "paused" ? "Source paused" : "Source resumed");

  const remove = (s: Source) => {
    if (!window.confirm(`Remove ${s.label || SOURCE_KIND_LABELS[s.kind]}? Its reviews stay in the feed.`)) return;
    void act(`del:${s.id}`, () => apiFetch(hotelPath(slug, `/reviews/sources/${s.id}`), { method: "DELETE" }), "Source removed");
  };

  if (res.loading && !res.data) return <Card><Skeleton lines={5} /></Card>;
  if (res.error || !res.data) {
    return (
      <Card>
        <EmptyState title="Could not load sources" body={res.error ? errText(res.error) : undefined}
          action={<Button onClick={res.reload}>Try again</Button>} />
      </Card>
    );
  }

  const data = res.data;
  const gbp = data.sources.find(s => s.kind === "gbp") ?? null;
  const others = data.sources.filter(s => s.kind !== "gbp");

  return (
    <div className="cx-stack">
      <GoogleCard slug={slug} source={gbp} canManage={canManage} onChanged={refresh} />

      <Card
        title="Connected sources"
        subtitle="Synced every morning at 05:30 hotel time."
        actions={canManage && data.sources.length > 0 ? (
          <Button size="sm" variant="secondary" loading={busy === "sync:all"} onClick={() => void sync()}>Sync now</Button>
        ) : undefined}
      >
        {others.length === 0 ? (
          <EmptyState title="No other sources yet" body="Add Tripadvisor or Google Maps below, or import an OTA export." />
        ) : (
          <ul className="rev-list">
            {others.map(s => (
              <li key={s.id} className="rev-card" style={{ cursor: "default" }}>
                <div className="rev-card__head">
                  <span className="rev-card__who">{s.label || SOURCE_KIND_LABELS[s.kind]}</span>
                  <Badge>{SOURCE_KIND_LABELS[s.kind]}</Badge>
                  <Badge tone={STATUS_TONE[s.status]} dot>{s.status}</Badge>
                  <span className="rev-spacer" />
                  <span className="rev-card__meta">
                    {s.last_synced_at ? `Synced ${fmt.relative(s.last_synced_at)}` : "Not synced yet"}
                  </span>
                </div>
                {s.last_error && <div className="rev-notice rev-notice--bad">{s.last_error}</div>}
                {canManage && (s.kind === "serpapi_google" || s.kind === "tripadvisor") && (
                  <div className="rev-actions">
                    <Button size="sm" variant="secondary" loading={busy === `sync:${s.id}`} onClick={() => void sync(s.id)}>Sync</Button>
                    <Button size="sm" variant="ghost" loading={busy === `status:${s.id}`}
                      onClick={() => void setStatus(s, s.status === "paused" ? "active" : "paused")}>
                      {s.status === "paused" ? "Resume" : "Pause"}
                    </Button>
                    <Button size="sm" variant="danger" loading={busy === `del:${s.id}`} onClick={() => remove(s)}>Remove</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {canManage && <AddSource slug={slug} payload={data} onChanged={refresh} />}

      <Card title="Forwarded review emails"
        subtitle="Forward Booking.com, Agoda or Expedia review notifications here and they join the feed.">
        {data.inbound_address ? (
          <div className="rev-row">
            <code className="rev-code">{data.inbound_address}</code>
            <Button size="sm" variant="secondary" onClick={async () => {
              if (await copyText(data.inbound_address ?? "")) toast.success("Address copied");
              else toast.error("Could not copy", "Select the address and copy it by hand.");
            }}>Copy address</Button>
          </div>
        ) : (
          <p className="rev-muted">An inbound address is not set up for this hotel yet. Ask Clefs support to enable it.</p>
        )}
        <p className="rev-muted" style={{ marginBottom: 0 }}>
          Only mail from your team, the platforms themselves, or senders listed in reviews settings is accepted.
        </p>
      </Card>

      {canManage && <ImportPanel slug={slug} onImported={refresh} />}
    </div>
  );
}
