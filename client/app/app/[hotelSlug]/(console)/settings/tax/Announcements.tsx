"use client";

/* The 2026 Dubai announcements: the Tourism Dirham and municipality fee
   exemption and the 3-month remittance deferral. They were announced but
   never published as a circular Clefs could verify, so they are seeded as
   ANNOUNCED_UNVERIFIED and count for a hotel only after its owner switches
   them on here (an audited hotel_tax_overrides row). Switching on needs an
   explicit acknowledgement: applying an exemption that turns out not to
   exist means under-collecting a fee the hotel still owes. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Modal, useToast } from "../../../../../ui";
import { errorText } from "../../../../../ingest";
import { FEE_LABEL, taxPath, type OverrideBody } from "./api";
import { announcementGroups, type FeeRule } from "./convention";

export interface AnnouncementsProps {
  slug: string;
  rules: readonly FeeRule[];
  canWrite: boolean;
  onChanged: () => void;
}

function groupTitle(rules: readonly FeeRule[]): string {
  const deferral = rules.some(r => r.calc_mode === "REMIT_DEFERRAL");
  if (deferral) return "Remittance deferral";
  if (rules.every(r => Number(r.rate) === 0)) return "Fee exemption";
  return rules[0]?.label ?? "Announcement";
}

export function Announcements({ slug, rules, canWrite, onChanged }: AnnouncementsProps) {
  const fmt = useFormat();
  const toast = useToast();
  const groups = announcementGroups(rules);
  const [confirm, setConfirm] = useState<{ key: string; rules: FeeRule[] } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const setApply = async (key: string, list: readonly FeeRule[], apply: boolean) => {
    setBusy(key);
    try {
      // One override per rule of the announcement: the exemption covers both
      // the TD and the MF rows, and applying half of it would be wrong.
      for (const r of list) {
        const body: OverrideBody = {
          fee_rule_id: r.id, rule_key: r.rule_key, apply,
          note: apply ? "Owner applied the 2026 announcement from Settings > Tax" : "Owner switched it off",
        };
        await api.post(taxPath(slug, "/overrides"), body);
      }
      toast.success(apply ? "Announcement applied" : "Announcement switched off",
        apply ? "Stays on its dates are now split without these fees." : undefined);
      onChanged();
    } catch (err) {
      toast.error("Couldn't change it", errorText(err));
      onChanged();
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  };

  if (!groups.length) return <p className="tax-note">No announced changes for this hotel&apos;s emirate.</p>;

  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      {groups.map(g => {
        const applied = g.rules.every(r => r.override?.apply === true);
        const partly = !applied && g.rules.some(r => r.override?.apply === true);
        const src = g.rules.find(r => r.source_url)?.source_url;
        const from = g.rules.map(r => r.effective_from).sort()[0];
        const to = g.rules.map(r => r.effective_to).filter(Boolean).sort().pop() ?? null;
        return (
          <div key={g.key} className="tax-group" data-applied={applied || undefined}>
            <div className="cx-row" style={{ justifyContent: "space-between" }}>
              <div>
                <div style={{ fontWeight: 600 }}>{groupTitle(g.rules)}</div>
                <div className="tax-sub">
                  {g.rules.map(r => FEE_LABEL[r.fee_code] ?? r.fee_code).join(" and ")}
                  {" · "}from {fmt.date(from, "medium")}{to ? ` to ${fmt.date(to, "medium")}` : ", no end date announced"}
                </div>
              </div>
              <div className="cx-row">
                <Badge tone="warn">Unverified</Badge>
                <Badge tone={applied ? "gold" : "neutral"} dot>{applied ? "Applied" : partly ? "Partly applied" : "Not applied"}</Badge>
              </div>
            </div>
            <p className="tax-note">
              {g.rules[0]?.label}. {src && <a className="tax-src" href={src} target="_blank" rel="noopener noreferrer">Read the announcement</a>}
            </p>
            {canWrite && (
              <div className="cx-row">
                {applied
                  ? <Button size="sm" variant="secondary" loading={busy === g.key} onClick={() => void setApply(g.key, g.rules, false)}>Switch off</Button>
                  : <Button size="sm" loading={busy === g.key} onClick={() => setConfirm(g)}>Apply to my hotel</Button>}
              </div>
            )}
          </div>
        );
      })}

      <Modal open={confirm !== null} onClose={() => setConfirm(null)} title="Apply an unverified announcement?"
             footer={
               <div className="cx-row" style={{ justifyContent: "flex-end" }}>
                 <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
                 <Button loading={busy !== null} onClick={() => confirm && void setApply(confirm.key, confirm.rules, true)}>
                   I have checked it, apply
                 </Button>
               </div>
             }>
        <p className="tax-note" style={{ fontSize: "0.9rem" }}>
          Clefs could not verify this announcement against a published circular. Apply it only if Dubai Economy and
          Tourism or your accountant has confirmed it for your property. While it is applied, stays on its dates are
          split without these fees, and every screen that uses them shows a banner. Your name and the time are recorded.
        </p>
      </Modal>
    </div>
  );
}
