"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One pilot: live scorecard (to today, or the frozen outcome once the pilot
   is completed), the daily checkpoints that feed the "pilot off track"
   signal, and the status moves the server allows
   (draft > active > completed > converted, any open state > cancelled).
   Every scorecard value links to the metric query that produced it.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { api, hotelPath, useApi } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useHotel } from "../../../../../../lib/hotel";
import { useSession } from "../../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Skeleton, useToast } from "../../../../../../ui";
import { dayLabel, errText, moduleLabel, PILOT_STATUS, ratePct, trustApi, trustHref } from "../../lib";
import type { MetricInfo, Pilot, PilotDetail, PilotStatus } from "../../types";
import PilotForm from "../PilotForm";
import Scorecard from "./Scorecard";

const NEXT: Record<PilotStatus, { status: PilotStatus; label: string; confirm?: string }[]> = {
  draft: [{ status: "active", label: "Start pilot" }],
  active: [{ status: "completed", label: "Complete pilot", confirm: "Completing freezes the scorecard and the ROI report. Continue?" }],
  completed: [{ status: "converted", label: "Mark converted" }],
  converted: [],
  cancelled: [],
};

export default function PilotDetailPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const params = useParams<{ id: string }>();
  const id = Number(params?.id);
  const valid = Number.isInteger(id) && id > 0;
  const allowed = can("trust.pilots.view");
  const canManage = can("trust.pilots.manage");

  const res = useApi<PilotDetail>(allowed && valid ? trustApi(slug, `/pilots/${id}`) : null, [slug, id]);
  const metrics = useApi<MetricInfo[]>(allowed ? hotelPath(slug, "/metrics") : null, [slug]);
  const info = useMemo(() => new Map((metrics.data ?? []).map(m => [m.key, m])), [metrics.data]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  if (!allowed) return <Card><EmptyState title="Pilots are for owners, GMs and finance" /></Card>;
  if (!valid) return <Card><EmptyState title="Pilot not found" /></Card>;
  if (res.error) {
    return <Card><EmptyState title={res.error.status === 404 ? "Pilot not found" : "Could not load the pilot"} body={res.error.message}
                             action={<Button variant="secondary" size="sm" href={trustHref(slug, "/pilots")}>All pilots</Button>} /></Card>;
  }
  const p = res.data;
  if (!p) return <Card><Skeleton lines={10} /></Card>;

  async function move(status: PilotStatus, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(status);
    try {
      await api.post<Pilot>(trustApi(slug, `/pilots/${id}/status`), { status });
      toast.success(`Pilot ${PILOT_STATUS[status]?.label.toLowerCase() ?? status}`);
      res.reload();
    } catch (e) {
      toast.error("Could not change the status", errText(e));
    } finally {
      setBusy(null);
    }
  }

  async function checkpoint() {
    setBusy("checkpoint");
    try {
      const r = await api.post<{ score: string | null; on_track: boolean | null }>(trustApi(slug, `/pilots/${id}/checkpoint`));
      toast.info("Checkpoint recorded", `Score ${ratePct(r.score)}${r.on_track === false ? ", off track" : ""}.`);
      res.reload();
    } catch (e) {
      toast.error("Checkpoint failed", errText(e));
    } finally {
      setBusy(null);
    }
  }

  const frozen = p.status === "completed" || p.status === "converted" || p.status === "cancelled";
  const open = p.status === "draft" || p.status === "active" || p.status === "completed";

  return (
    <>
      <div className="cx-row" style={{ gap: 8, flexWrap: "wrap" }}>
        <Link href={trustHref(slug, "/pilots")} className="trs-small">All pilots</Link>
      </div>
      <Card
        title={p.name}
        subtitle={`${dayLabel(p.start_date, fmt.locale)} to ${dayLabel(p.end_date, fmt.locale)} · ${p.modules.map(moduleLabel).join(", ") || "No modules"}`}
        actions={<Badge tone={PILOT_STATUS[p.status]?.tone ?? "neutral"}>{PILOT_STATUS[p.status]?.label ?? p.status}</Badge>}
      >
        <dl className="trs-kv">
          <dt>Baseline</dt>
          <dd>{p.baseline_start ? `${dayLabel(p.baseline_start, fmt.locale)} to ${dayLabel(p.baseline_end, fmt.locale)}` : "None (absolute targets only)"}</dd>
          <dt>Price after the pilot</dt>
          <dd>{p.conversion_price_minor != null
            ? `${fmt.money(p.conversion_price_minor, { minor: true, currency: p.currency })} per ${p.terms?.billing_period === "year" ? "year" : "month"}`
            : "Not set"}</dd>
          <dt>On track at</dt><dd>{ratePct(p.terms?.on_track_threshold ?? 0.7)} of weighted criteria met</dd>
          {p.created_by && <><dt>Created by</dt><dd>{p.created_by}, {fmt.date(p.created_at, "medium")}</dd></>}
        </dl>
        <div className="cx-row" style={{ gap: 8, flexWrap: "wrap", marginTop: 14 }}>
          <Button size="sm" href={trustHref(slug, `/pilots/${id}/report`)}>ROI report</Button>
          {canManage && !frozen && <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>Edit</Button>}
          {canManage && p.status === "active" && (
            <Button size="sm" variant="secondary" loading={busy === "checkpoint"} disabled={!!busy} onClick={checkpoint}>Checkpoint now</Button>
          )}
          {canManage && NEXT[p.status].map(n => (
            <Button key={n.status} size="sm" variant="secondary" loading={busy === n.status} disabled={!!busy}
                    onClick={() => move(n.status, n.confirm)}>{n.label}</Button>
          ))}
          {canManage && open && (
            <Button size="sm" variant="ghost" loading={busy === "cancelled"} disabled={!!busy}
                    onClick={() => move("cancelled", "Cancel this pilot? This cannot be undone.")}>Cancel pilot</Button>
          )}
        </div>
      </Card>
      <Scorecard slug={slug} pilot={p} info={info} />
      {editing && (
        <PilotForm slug={slug} pilot={p} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); res.reload(); }} />
      )}
    </>
  );
}
