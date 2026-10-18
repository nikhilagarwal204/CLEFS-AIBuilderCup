"use client";

/* Add or edit one par row (amenities.manage). Typing a par pins it, so
   the weekly recompute will not overwrite it; "Use suggested" unpins and
   lets recompute own the number again. */

import { useState } from "react";
import { ApiError, api, hotelPath } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Field, Input, Modal, Select, useToast } from "../../../../../ui";
import { qtyText } from "./lib";
import type { Item, Location, Par } from "./types";

const isQty = (s: string) => /^\d{1,6}(\.\d{1,2})?$/.test(s.trim());

export default function ParEditor({ row, locations, items, onClose, onSaved }: {
  row: Par | null;
  locations: Location[];
  items: Item[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [locationId, setLocationId] = useState(String(row?.location_id ?? locations[0]?.id ?? ""));
  const [itemId, setItemId] = useState(String(row?.item_id ?? items.find(i => i.active)?.id ?? ""));
  const [parQty, setParQty] = useState(row?.par_qty ?? "");
  const [lead, setLead] = useState(row?.lead_days !== null && row?.lead_days !== undefined ? String(row.lead_days) : "");
  const [busy, setBusy] = useState<"save" | "unpin" | "delete" | null>(null);
  const suggested = row?.computed?.suggested_par;

  const call = async (kind: "save" | "unpin" | "delete") => {
    setBusy(kind);
    try {
      if (kind === "delete" && row) {
        await api.del(hotelPath(slug, `/amenities/par/${row.location_id}/${row.item_id}`));
      } else {
        const body: Record<string, unknown> = { location_id: Number(locationId), item_id: Number(itemId) };
        if (kind === "unpin") body.pinned = false;
        else if (parQty.trim()) body.par_qty = parQty.trim();
        if (lead.trim() && isQty(lead)) body.lead_days = lead.trim();
        await api.put<Par>(hotelPath(slug, "/amenities/par"), body);
      }
      toast.success(kind === "delete" ? "Par removed" : kind === "unpin" ? "Par follows the suggestion again" : "Par saved");
      onSaved();
    } catch (err) {
      toast.error("Not saved", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const valid = Boolean(locationId && itemId) && (!parQty.trim() || isQty(parQty)) && (!lead.trim() || isQty(lead));

  return (
    <Modal open onClose={onClose} title={row ? `Par for ${row.item_name}` : "Add a par level"} dismissible={busy === null}
           footer={<>
             {row && <Button variant="danger" onClick={() => call("delete")} loading={busy === "delete"}>Remove</Button>}
             <span className="amn-spacer" />
             {row?.pinned && <Button variant="ghost" onClick={() => call("unpin")} loading={busy === "unpin"}>Use suggested</Button>}
             <Button variant="primary" onClick={() => call("save")} loading={busy === "save"} disabled={!valid}>Save</Button>
           </>}>
      <div className="amn-form">
        <Field label="Location">
          <Select value={locationId} disabled={Boolean(row)} onChange={e => setLocationId(e.target.value)}>
            {locations.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
          </Select>
        </Field>
        <Field label="Item">
          <Select value={itemId} disabled={Boolean(row)} onChange={e => setItemId(e.target.value)}>
            {row && <option value={row.item_id}>{row.item_name}</option>}
            {!row && items.filter(i => i.active).map(i => <option key={i.id} value={i.id}>{i.name_en}</option>)}
          </Select>
        </Field>
        <Field label="Par quantity" hint={suggested !== undefined && suggested !== null
          ? `Suggested ${qtyText(suggested)}. A typed par is kept through recompute.` : "Leave blank to let recompute set it."}
               error={parQty.trim() && !isQty(parQty) ? "A whole or decimal number" : undefined}>
          <Input inputMode="decimal" value={parQty} onChange={e => setParQty(e.target.value)} />
        </Field>
        <Field label="Lead days" hint="Days from ordering to the item arriving here"
               error={lead.trim() && !isQty(lead) ? "A number of days" : undefined}>
          <Input inputMode="decimal" value={lead} onChange={e => setLead(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
