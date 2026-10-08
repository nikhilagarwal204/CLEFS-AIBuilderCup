"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One finding (02 spec 3.10 findings/[id]): what is wrong and how much
   (header), how the amount was computed (CalcTrace), what it rests on
   (EvidenceList), what to do (ActionBar) and everything that happened to
   it since (the recovery ledger for this finding, credits included).
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { LuArrowLeft } from "react-icons/lu";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Card, ConfidencePill, CountdownChip, Skeleton } from "../../../../../ui";
import { detectorLabel, DISMISS_REASONS, num, OPEN_STATUSES, otaLabel, pgApi, pgHref, STATUS_LABEL } from "../lib";
import type { FindingDetail as Detail } from "../types";
import { ClassBadge, ErrorNote, Money, PacketBadge, StatusBadge, useCanManage } from "./bits";
import { CalcTrace } from "./CalcTrace";
import { EvidenceList } from "./EvidenceList";
import { FindingActionBar } from "./FindingActionBar";

export default function FindingDetail({ id }: { id: string }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const canManage = useCanManage();
  const q = useApi<Detail>(/^\d+$/.test(id) ? pgApi(slug, `/findings/${id}`) : null);
  const f = q.data;

  const back = (
    <Link href={pgHref(slug, "/findings")} className="cx-link pg-row" style={{ gap: 6 }}>
      <LuArrowLeft aria-hidden="true" /> All findings
    </Link>
  );
  if (!/^\d+$/.test(id)) return <>{back}<ErrorNote error={new Error("That finding link is not valid.")} /></>;
  if (q.loading && !f) return <>{back}<Card><Skeleton lines={6} /></Card></>;
  if (q.error || !f) return <>{back}<ErrorNote error={q.error ?? new Error("Finding not found.")} /></>;

  const open = OPEN_STATUSES.includes(f.status);
  const reason = DISMISS_REASONS.find(r => r.value === f.dismiss_reason)?.label;

  return (
    <>
      {back}
      <Card gold title={detectorLabel(f.detector)}
            subtitle={`${otaLabel(f.ota_code)}${f.booking_ref ? `, booking ${f.booking_ref}` : ""} · ${f.detector} · found ${fmt.date(f.created_at)}`}
            actions={<StatusBadge status={f.status} />}>
        <div className="pg-between" style={{ alignItems: "flex-start" }}>
          <dl className="pg-kv">
            <dt>Amount</dt>
            <dd className="pg-strong"><Money minor={f.amount_minor} currency={f.currency} /></dd>
            {f.recovered_minor > 0 && (<><dt>Recovered</dt><dd><Money minor={f.recovered_minor} currency={f.currency} /></dd></>)}
            <dt>Class</dt><dd><ClassBadge klass={f.class} /></dd>
            <dt>Confidence</dt><dd><ConfidencePill value={num(f.confidence)} showValue /></dd>
            <dt>Deadline</dt>
            <dd>
              {f.deadline_at ? (
                <span className="pg-row" style={{ gap: 8 }}>
                  {fmt.dateTime(f.deadline_at, "medium")}
                  {open && <CountdownChip due={f.deadline_at} warnWithinHours={72} />}
                </span>
              ) : "No deadline"}
            </dd>
            {f.dispute_packet && (
              <>
                <dt>Dispute packet</dt>
                <dd className="pg-row" style={{ gap: 8 }}>
                  <Link className="cx-link" href={pgHref(slug, `/disputes/${f.dispute_packet.id}`)}>#{f.dispute_packet.id}</Link>
                  <PacketBadge status={f.dispute_packet.status} />
                </dd>
              </>
            )}
            {f.status === "DISMISSED" && (<><dt>Dismissed</dt><dd>{reason ?? f.dismiss_reason}{f.dismiss_note ? `: ${f.dismiss_note}` : ""}</dd></>)}
            {f.verified_by && (<><dt>Verified by</dt><dd>{f.verified_by.toLowerCase()}</dd></>)}
          </dl>
        </div>
        {canManage && (
          <div style={{ marginTop: 16 }}>
            <FindingActionBar slug={slug} finding={f} onChange={d => q.setData(d)} />
          </div>
        )}
      </Card>

      <div className="pg-grid2">
        <Card title="How the amount was calculated" subtitle="Each step in the order the check computed it.">
          <CalcTrace steps={f.calc} currency={f.currency} confidence={f.confidence} />
        </Card>
        <Card title="Evidence" subtitle="The records and files this finding rests on.">
          <EvidenceList evidence={f.evidence} slug={slug} />
        </Card>
      </div>

      <Card title="History" subtitle="The recovery ledger for this finding. Credits are their own entries.">
        {f.events.length === 0 ? <div className="pg-faint">No history yet.</div> : (
          <ul className="pg-rail">
            {f.events.map(e => (
              <li key={e.id} className="pg-rail__item">
                <div className="pg-rail__title">
                  {e.channel === "credit" ? "Credit" : `${e.from_status ? `${STATUS_LABEL[e.from_status]} to ` : ""}${STATUS_LABEL[e.to_status] ?? e.to_status}`}
                </div>
                {e.amount_minor ? <Money minor={e.amount_minor} currency={f.currency} /> : <span />}
                <div className="pg-rail__meta">
                  {fmt.dateTime(e.at)}{e.actor ? ` · ${e.actor}` : ""}{e.channel && e.channel !== "credit" ? ` · ${e.channel}` : ""}
                  {e.note ? ` · ${e.note}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
