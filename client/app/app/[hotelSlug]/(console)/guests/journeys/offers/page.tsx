"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Journey offers: the owner-approved "campaign" behind the pre-arrival
   offer (J3) and the rebooking offer (J8). An edit withdraws the approval
   (the owner approves exactly what goes out). Titles may not ask for
   reviews or contain an em dash; the server lint enforces both.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useHotel } from "../../../../../../lib/hotel";
import { useSession } from "../../../../../../lib/session";
import { Badge, Button, Card, Table, useToast, type Column } from "../../../../../../ui";
import { errText, jrnApi, PERM, stepLabel } from "../../lib";
import type { Offer } from "../../types";
import OfferForm from "./OfferForm";

export default function OffersPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<Offer[]>(jrnApi(slug, "/offers"), [slug]);
  const [editing, setEditing] = useState<Offer | "new" | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const manage = can(PERM.jManage);

  async function act(o: Offer, what: "approve" | "deactivate") {
    if (what === "deactivate" && !window.confirm(`Stop using "${o.title_en}"? Planned messages will not include it.`)) return;
    setBusy(o.id);
    try {
      if (what === "approve") await api.post(jrnApi(slug, `/offers/${o.id}/approve`));
      else await api.del(jrnApi(slug, `/offers/${o.id}`));
      toast.success(what === "approve" ? "Offer approved" : "Offer stopped");
      res.reload();
    } catch (err) {
      toast.error("Could not update the offer", errText(err));
    } finally {
      setBusy(null);
    }
  }

  const cols: Column<Offer>[] = [
    { key: "title", header: "Offer", render: o => (
      <div>
        <strong>{o.title_en}</strong>
        {o.title_ar && <div className="gst-small gst-muted" dir="rtl" lang="ar">{o.title_ar}</div>}
      </div>
    ) },
    { key: "step", header: "Used by", render: o => stepLabel(o.step) },
    { key: "value", header: "Value", render: o => [
      o.price ? fmt.money(o.price, { currency: o.currency }) : null,
      o.discount_pct ? `${Number(o.discount_pct)}% off` : null,
    ].filter(Boolean).join(" · ") || "-" },
    { key: "valid", header: "Valid", render: o => <span className="gst-small">{fmt.date(`${o.valid_from}T12:00:00Z`)} to {fmt.date(`${o.valid_to}T12:00:00Z`)}</span> },
    { key: "code", header: "Code prefix", hideOnMobile: true, render: o => (o.code_prefix ? <span className="gst-mono">{o.code_prefix}</span> : "-") },
    { key: "state", header: "Status", render: o => !o.active ? <Badge>Stopped</Badge>
      : o.approved_at ? <Badge tone="ok">Approved</Badge> : <Badge tone="warn">Needs approval</Badge> },
    ...(manage ? [{ key: "act", header: "", align: "right" as const, render: (o: Offer) => o.active ? (
      <span className="cx-row" style={{ justifyContent: "flex-end" }} onClick={e => e.stopPropagation()}>
        {!o.approved_at && <Button size="sm" loading={busy === o.id} onClick={() => act(o, "approve")}>Approve</Button>}
        <Button size="sm" variant="ghost" onClick={() => setEditing(o)}>Edit</Button>
        <Button size="sm" variant="ghost" loading={busy === o.id} onClick={() => act(o, "deactivate")}>Stop</Button>
      </span>
    ) : null }] : []),
  ];

  return (
    <Card
      title="Offers"
      subtitle="What the pre-arrival and rebooking messages offer. Only approved, active offers in their dates are used."
      actions={manage ? <Button size="sm" onClick={() => setEditing("new")}>New offer</Button> : undefined}
    >
      <Table columns={cols} rows={res.data ?? []} rowKey={o => o.id} loading={res.loading && !res.data}
        empty={res.error ? res.error.message : "No offers yet. Without an approved offer the offer steps are skipped."}
        caption="Journey offers" />
      {editing && (
        <OfferForm
          offer={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); res.reload(); }}
        />
      )}
    </Card>
  );
}
