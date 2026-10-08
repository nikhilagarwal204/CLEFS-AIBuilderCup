"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Rebooking pipeline: recent check-outs and where each stands for the
   rebooking offer (journey step J8, sent at check-out + 30 and + 60 days).
   Only guests with a separate marketing yes on their OWN contact are
   eligible; OTA stays without one are never marketed to (Canon 19), and
   the offer goes out only 09:00-18:00 hotel time, at most 2 per 30 days.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Card, Segmented, StatTile, Table, type Column } from "../../../../../ui";
import { gstHref, gstApi, humanize, PERM, statusTone } from "../lib";
import type { RebookRow } from "../types";
import CodesCard from "./CodesCard";

type Days = 35 | 70 | 120;

export default function RebookingPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const router = useRouter();
  const [days, setDays] = useState<Days>(70);
  const res = useApi<{ day: string; items: RebookRow[] }>(withQuery(gstApi(slug, "/rebooking"), { days }), [slug, days]);
  const rows = res.data?.items ?? [];
  const eligible = rows.filter(r => r.eligible).length;
  const ota = rows.filter(r => r.ota && !r.eligible).length;
  const sent = rows.filter(r => r.j8.some(p => ["sent", "delivered", "read", "replied"].includes(p.status))).length;

  const cols: Column<RebookRow>[] = [
    { key: "room", header: "Room", render: r => <strong>{r.room || "-"}</strong> },
    { key: "guest", header: "Guest", render: r => r.profile ? (
      <Link href={gstHref(slug, `/profiles?id=${r.profile.id}`)} onClick={e => e.stopPropagation()}>
        {r.profile.first_name || "Guest"}{r.profile.vip ? " (VIP)" : ""}
      </Link>
    ) : <span className="gst-faint">No profile</span> },
    { key: "out", header: "Checked out", render: r => <span>{fmt.date(r.check_out)} <span className="gst-small gst-muted">({r.days_since} d)</span></span> },
    { key: "src", header: "Booked via", render: r => (
      <span className="cx-row" style={{ gap: 6 }}>{r.source || "-"}{r.ota && <Badge tone="neutral">OTA</Badge>}</span>
    ) },
    { key: "mkt", header: "Offer consent", render: r => r.suppressed ? <Badge tone="warn">Opted out</Badge> : (
      <span className="gst-chips">
        {r.marketing.whatsapp && <Badge tone="ok">WhatsApp</Badge>}
        {r.marketing.email && <Badge tone="ok">Email</Badge>}
        {!r.marketing.whatsapp && !r.marketing.email && <span className="gst-faint gst-small">None</span>}
      </span>
    ) },
    { key: "j8", header: "Rebooking offer", render: r => r.j8.length === 0
      ? <span className="gst-small gst-faint">{r.eligible ? "Not planned yet" : "Not eligible"}</span>
      : (
        <span className="gst-chips">
          {r.j8.map(p => (
            <Badge key={p.seq} tone={statusTone(p.status)} title={p.skip_reason ? humanize(p.skip_reason) : p.due_at ? `Due ${fmt.dateTime(p.due_at)}` : undefined}>
              Offer {p.seq}: {humanize(p.skip_reason && p.status === "skipped" ? p.skip_reason : p.status)}
            </Badge>
          ))}
        </span>
      ) },
  ];

  return (
    <>
      <div className="cx-grid cx-grid--3">
        <StatTile label="Recent check-outs" loading={res.loading && !res.data} value={fmt.number(rows.length)} sub={`Last ${days} days`} />
        <StatTile label="Eligible for an offer" loading={res.loading && !res.data} value={fmt.number(eligible)}
          sub={`${fmt.number(ota)} OTA stays without their own consented contact`} />
        <StatTile label="Offer sent" loading={res.loading && !res.data} value={fmt.number(sent)} sub="At least one J8 message out" />
      </div>
      <Card
        title="Rebooking pipeline"
        subtitle="Offers are sent by the Rebooking offer journey step once its template and offer are approved."
        actions={<Segmented<Days> ariaLabel="Window" size="sm" value={days} onChange={setDays}
          options={[{ value: 35, label: "35 d" }, { value: 70, label: "70 d" }, { value: 120, label: "120 d" }]} />}
      >
        <Table
          columns={cols}
          rows={rows}
          rowKey={r => r.stay_id}
          onRowClick={can(PERM.jView) ? (r => { if (r.reservation_id != null) router.push(gstHref(slug, `/journeys/timeline/${r.reservation_id}`)); }) : undefined}
          loading={res.loading && !res.data}
          empty={res.error ? res.error.message : "No check-outs in this window."}
          caption="Rebooking pipeline"
        />
      </Card>
      <CodesCard />
    </>
  );
}
