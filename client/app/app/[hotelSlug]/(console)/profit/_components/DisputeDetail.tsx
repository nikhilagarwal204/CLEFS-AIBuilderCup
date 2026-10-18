"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One dispute packet (02 spec 3.7, 3.10 disputes/[id]): the preview an
   approver reads before signing (every claim with its booking, contract
   clause, calculation and evidence), the text to paste into the OTA's
   extranet (1,000 characters or less), the email version, the print and
   PDF versions, and the approve / sent / outcome decisions.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import Link from "next/link";
import { LuArrowLeft, LuCopy, LuFileText, LuPrinter } from "react-icons/lu";
import { API_PREFIX, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Skeleton, useToast } from "../../../../../ui";
import { otaLabel, pgApi, pgHref } from "../lib";
import type { Packet, PacketItem } from "../types";
import { ErrorNote, Money, PacketBadge, StatusBadge, useCanManage } from "./bits";
import { CalcTrace } from "./CalcTrace";
import { EvidenceList } from "./EvidenceList";
import { PacketActions } from "./PacketActions";

function Item({ it, slug }: { it: PacketItem; slug: string }) {
  const fmt = useFormat();
  const [more, setMore] = useState(false);
  const stay = [it.check_in, it.check_out].filter(Boolean).map(d => fmt.date(d as string)).join(" to ");
  return (
    <div className="pg-item">
      <div className="pg-between">
        <div>
          <div className="pg-strong">{it.label}</div>
          <div className="pg-faint">
            Booking {it.booking_ref ?? "n/a"}{it.pms_res_id ? ` (PMS ${it.pms_res_id})` : ""}
            {it.guest_name ? ` · ${it.guest_name}` : ""}{stay ? ` · ${stay}` : ""}{it.doc_no ? ` · statement ${it.doc_no}` : ""}
          </div>
        </div>
        <div className="pg-row" style={{ gap: 8 }}>
          <StatusBadge status={it.status} />
          <span className="pg-strong"><Money minor={it.amount_minor} currency={it.currency} /></span>
        </div>
      </div>
      {it.clause_ref ? (
        <blockquote className="pg-clause">
          {it.clause_text || "See the contract clause."}
          <cite>{it.clause_ref}</cite>
        </blockquote>
      ) : <div className="pg-faint">No contract clause on file for this claim.</div>}
      <div className="pg-row">
        <Button size="sm" variant="ghost" onClick={() => setMore(m => !m)} aria-expanded={more}>
          {more ? "Hide calculation and evidence" : "Show calculation and evidence"}
        </Button>
        <Link className="cx-link" href={pgHref(slug, `/findings/${it.id}`)}>Open finding</Link>
      </div>
      {more && (
        <div className="pg-grid2">
          <CalcTrace steps={it.calc} currency={it.currency} />
          <EvidenceList evidence={it.evidence} slug={slug} />
        </div>
      )}
    </div>
  );
}

export default function DisputeDetail({ id }: { id: string }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = useCanManage();
  const valid = /^\d+$/.test(id);
  const q = useApi<Packet>(valid ? pgApi(slug, `/disputes/${id}`) : null);
  const p = q.data;

  const back = (
    <Link href={pgHref(slug, "/disputes")} className="cx-link pg-row" style={{ gap: 6 }}>
      <LuArrowLeft aria-hidden="true" /> All dispute packets
    </Link>
  );
  if (!valid) return <>{back}<ErrorNote error={new Error("That packet link is not valid.")} /></>;
  if (q.loading && !p) return <>{back}<Card><Skeleton lines={8} /></Card></>;
  if (q.error || !p) return <>{back}<ErrorNote error={q.error ?? new Error("Packet not found.")} /></>;

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast({ tone: "ok", title: `${what} copied` });
    } catch {
      toast({ tone: "warn", title: "Could not copy", body: "Select the text and copy it by hand." });
    }
  }

  const extranet = p.extranet_text ?? "";
  const items = p.items ?? [];
  const stamp = p.approved_at
    ? `Approved by ${p.approved_by_name ?? "the owner"} on ${fmt.dateTime(p.approved_at, "medium")}`
    : "Not approved yet";

  return (
    <>
      {back}
      <Card gold title={`Dispute packet #${p.id}: ${otaLabel(p.ota_code)}`}
            subtitle={`${items.length || p.finding_ids.length} claim${(items.length || p.finding_ids.length) === 1 ? "" : "s"} · drafted ${fmt.date(p.created_at)}`}
            actions={<PacketBadge status={p.status} />}>
        <div className="cx-stack">
          <dl className="pg-kv">
            <dt>Claimed</dt><dd className="pg-strong"><Money minor={p.total_minor} currency={p.currency} /></dd>
            {(p.recovered_minor ?? 0) > 0 && (<><dt>Credited so far</dt><dd><Money minor={p.recovered_minor ?? 0} currency={p.currency} /></dd></>)}
            <dt>Approval</dt>
            <dd><Badge tone={p.approved_at ? "ok" : "warn"}>{stamp}</Badge></dd>
            {p.submitted_at && (<><dt>Sent</dt><dd>{fmt.dateTime(p.submitted_at, "medium")}</dd></>)}
            {p.follow_up_at && ["SUBMITTED", "ACKNOWLEDGED", "PARTIAL"].includes(p.status) && (
              <><dt>Next follow-up</dt><dd>{fmt.date(p.follow_up_at, "medium")}</dd></>
            )}
            {p.outcome_note && (<><dt>OTA answer</dt><dd>{p.outcome_note}</dd></>)}
          </dl>
          <div className="pg-row">
            <Button size="sm" icon={<LuPrinter aria-hidden="true" />} href={pgHref(slug, `/disputes/${p.id}/print`)}>Print version</Button>
            <a className="cx-btn cx-btn--secondary cx-btn--sm" href={`${API_PREFIX}${p.pdf_path}`} target="_blank" rel="noopener noreferrer">
              <LuFileText aria-hidden="true" /> PDF
            </a>
          </div>
          {canManage && <PacketActions slug={slug} packet={p} onChange={np => q.setData(prev => ({ ...prev, ...np, items: np.items ?? prev?.items }))} />}
        </div>
      </Card>

      <div className="pg-grid2">
        <Card title="Extranet message" subtitle={`${extranet.length} of 1,000 characters. Paste it into the OTA's support form.`}
              actions={extranet ? <Button size="sm" variant="ghost" icon={<LuCopy aria-hidden="true" />} onClick={() => copy(extranet, "Message")}>Copy</Button> : undefined}>
          {extranet ? <pre className="pg-pre">{extranet}</pre> : <div className="pg-faint">No extranet text for this packet.</div>}
        </Card>
        <Card title="Email version"
              actions={p.email_body ? <Button size="sm" variant="ghost" icon={<LuCopy aria-hidden="true" />}
                                              onClick={() => copy(`${p.email_subject ?? ""}\n\n${p.email_body ?? ""}`, "Email")}>Copy</Button> : undefined}>
          {p.email_body ? (
            <div className="cx-stack">
              <div className="pg-strong">{p.email_subject}</div>
              <pre className="pg-pre">{p.email_body}</pre>
            </div>
          ) : <div className="pg-faint">No email text for this packet.</div>}
        </Card>
      </div>

      <Card title="Claims" subtitle="Each claim with the contract clause it relies on. Open one to see the calculation and evidence.">
        {items.length ? items.map(it => <Item key={it.id} it={it} slug={slug} />) : <div className="pg-faint">No claims in this packet.</div>}
      </Card>
    </>
  );
}
