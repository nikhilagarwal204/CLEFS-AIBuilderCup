"use client";

/* Issuance lines, newest first, with a void for managers. A mistyped line
   is voided, never deleted (the server keeps it for the audit trail and
   re-settles that day), so a voided line stays visible, struck through. */

import { useState } from "react";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Modal, Table, useToast, type Column } from "../../../../../ui";
import { PERM_MANAGE } from "./AmenitiesShell";
import { floorLabel, qtyText, REASON_LABELS, shiftLabel } from "./lib";
import type { Line } from "./types";

export default function IssuanceLog({
  itemId, from, to, version = 0, title = "Issuance lines", limit = 100,
}: {
  itemId?: number;
  from?: string;
  to?: string;
  /** Bump to refetch after a new entry. */
  version?: number;
  title?: string;
  limit?: number;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canVoid = can(PERM_MANAGE);
  const [confirm, setConfirm] = useState<Line | null>(null);
  const [busy, setBusy] = useState(false);

  const qs = new URLSearchParams({ include_voided: "true", limit: String(limit) });
  if (itemId) qs.set("item_id", String(itemId));
  if (from) qs.set("from", from);
  if (to) qs.set("to", to);
  const log = useApi<Line[]>(hotelPath(slug, `/amenities/issuance?${qs.toString()}`), [slug, version]);

  const doVoid = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      const row = await api.post<Line>(hotelPath(slug, `/amenities/issuance/${confirm.id}/void`));
      log.setData(prev => (prev ?? []).map(l => (l.id === row.id ? { ...l, voided_at: row.voided_at } : l)));
      toast.success("Line voided", "That day's figures will be recalculated.");
      setConfirm(null);
    } catch (err) {
      toast.error("Could not void the line", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const cols: Column<Line>[] = [
    { key: "when", header: "When", render: l => (
      <span className="amn-rowtitle">
        <span>{fmt.dateTime(l.issued_at)}</span>
        <small>{shiftLabel(l.shift)}</small>
      </span>
    ) },
    // On one item's page the item column only repeats the page title.
    ...(itemId ? [] : [{ key: "item", header: "Item", render: (l: Line) => l.item_name ?? l.item_key ?? `#${l.item_id}` }]),
    { key: "qty", header: "Qty", align: "right", render: l => <span className="amn-mono">{qtyText(l.qty)}</span> },
    { key: "where", header: "Floor", hideOnMobile: true, render: l => (l.to_floor ? floorLabel(l.to_floor) : "") },
    { key: "who", header: "Issued by", render: l => l.staff_name ?? "" },
    { key: "reason", header: "Reason", render: l => (
      <span className="amn-chips">
        <Badge tone={l.reason === "RETURN" ? "info" : l.reason === "VIP" || l.reason === "DAMAGE" ? "gold" : "neutral"}>
          {REASON_LABELS[l.reason] ?? l.reason}
        </Badge>
        {l.voided_at && <Badge tone="bad">Voided</Badge>}
      </span>
    ) },
    ...(canVoid ? [{
      key: "act", header: "", align: "right" as const, render: (l: Line) => (l.voided_at ? null : (
        <Button size="sm" variant="ghost" onClick={() => setConfirm(l)} aria-label={`Void line ${l.id}`}>Void</Button>
      )),
    }] : []),
  ];

  return (
    <Card title={title} subtitle={from && to ? `${from} to ${to}` : undefined}>
      {log.error ? (
        <p className="amn-note" role="alert">{log.error.message}</p>
      ) : (
        <Table columns={cols} rows={log.data ?? []} rowKey={l => l.id} loading={log.loading && !log.data}
               caption={title} empty="No issuance logged in this window." />
      )}
      <Modal open={confirm !== null} onClose={() => setConfirm(null)} title="Void this line?" size="sm" dismissible={!busy}
             footer={<>
               <Button variant="ghost" onClick={() => setConfirm(null)} disabled={busy}>Keep it</Button>
               <Button variant="danger" onClick={doVoid} loading={busy}>Void line</Button>
             </>}>
        {confirm && (
          <p className="amn-note" style={{ marginTop: 0 }}>
            {qtyText(confirm.qty)} x {confirm.item_name ?? "item"} on {fmt.dateTime(confirm.issued_at)}
            {confirm.staff_name ? ` by ${confirm.staff_name}` : ""}. The line stays in the log, struck out, and no longer
            counts toward issued.
          </p>
        )}
      </Modal>
    </Card>
  );
}
