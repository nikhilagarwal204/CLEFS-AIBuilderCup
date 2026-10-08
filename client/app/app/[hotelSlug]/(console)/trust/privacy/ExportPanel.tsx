"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Owner's full data export: one zip with a CSV per table (plus hotels.csv
   and manifest.json), built by the trust.tenant_export task. The link is
   signed and lives 7 days; after that the file itself is purged.
   While an export is queued or running we poll every 5 s, and stop as soon
   as none is in flight, so an idle tab costs nothing.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { api, apiUrl, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, EmptyState, Table, useToast, type Column, type Tone } from "../../../../../ui";
import { errText, trustApi } from "../lib";
import type { TenantExport } from "../types";

const STATUS: Record<TenantExport["status"], { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "info" },
  running: { label: "Building", tone: "info" },
  ready: { label: "Ready", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
  expired: { label: "Expired", tone: "neutral" },
};

export default function ExportPanel({ slug }: { slug: string }) {
  const fmt = useFormat();
  const toast = useToast();
  const list = useApi<TenantExport[]>(trustApi(slug, "/exports"), [slug]);
  const [busy, setBusy] = useState(false);
  const inFlight = (list.data ?? []).some(e => e.status === "queued" || e.status === "running");
  const { reload } = list;

  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [inFlight, reload]);

  async function start() {
    setBusy(true);
    try {
      await api.post<TenantExport>(trustApi(slug, "/export"));
      toast.info("Export started", "This usually takes a minute or two. The link appears here when it is ready.");
      reload();
    } catch (e) {
      toast.error("Could not start the export", errText(e));
    } finally {
      setBusy(false);
    }
  }

  const cols: Column<TenantExport>[] = [
    { key: "created", header: "Requested", render: e => fmt.dateTime(e.created_at) },
    { key: "status", header: "Status", render: e => <Badge tone={STATUS[e.status]?.tone ?? "neutral"}>{STATUS[e.status]?.label ?? e.status}</Badge> },
    {
      key: "size", header: "Contents", hideOnMobile: true,
      render: e => {
        if (e.error) return <span style={{ color: "var(--cx-bad)" }}>{e.error}</span>;
        if (!e.tables) return "-";
        const t = Object.values(e.tables);
        return `${t.length} tables, ${fmt.number(t.reduce((a, b) => a + b, 0))} rows`;
      },
    },
    { key: "exp", header: "Link expires", hideOnMobile: true, render: e => (e.expires_at ? fmt.dateTime(e.expires_at) : "-") },
    {
      key: "dl", header: "", align: "right",
      render: e => (e.status === "ready" && e.download_url
        ? <Button size="sm" variant="secondary" href={apiUrl(e.download_url)}>Download</Button>
        : null),
    },
  ];

  return (
    <Card
      title="Export all hotel data"
      subtitle="A zip of every table that holds this hotel's data, one CSV each. Passwords, sessions and connection secrets are left out. Only the owner can start one."
      actions={<Button size="sm" loading={busy} disabled={inFlight} onClick={start}>{inFlight ? "Export in progress" : "Start export"}</Button>}
    >
      {list.error ? (
        <EmptyState title="Could not load exports" body={list.error.message} />
      ) : (
        <Table columns={cols} rows={list.data ?? []} rowKey={e => e.id} loading={list.loading && !list.data}
               empty={<EmptyState title="No exports yet" body="Links stay valid for 7 days." />} />
      )}
    </Card>
  );
}
