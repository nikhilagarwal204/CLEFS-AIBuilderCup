"use client";

/* Record a stock count for one location (amenities.issue). One field per
   item that has a par there; blank fields are not counted (a blank is "I
   did not count this", which is different from 0). The counter's name is
   stored with the count so the rule "never count your own floor" can be
   checked later. */

import { useState } from "react";
import { ApiError, api, hotelPath } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Drawer, Field, Input, useToast } from "../../../../../ui";
import { qtyText } from "./lib";
import type { Location, Par } from "./types";

const isCount = (s: string) => /^\d{1,6}(\.\d{1,2})?$/.test(s.trim());

export default function CountDrawer({ location, rows, onClose, onSaved }: {
  location: Location;
  rows: Par[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [values, setValues] = useState<Record<number, string>>({});
  const [by, setBy] = useState("");
  const [busy, setBusy] = useState(false);

  const filled = Object.entries(values).filter(([, v]) => v.trim() !== "");
  const bad = filled.some(([, v]) => !isCount(v));

  const save = async () => {
    if (!filled.length || bad) return;
    setBusy(true);
    try {
      await api.post(hotelPath(slug, "/amenities/stock-counts"), {
        lines: filled.map(([itemId, v]) => ({
          location_id: location.id, item_id: Number(itemId), qty: v.trim(), counted_by: by.trim() || null,
        })),
      });
      toast.success("Count saved", `${filled.length} item${filled.length === 1 ? "" : "s"} counted at ${location.name}.`);
      onSaved();
    } catch (err) {
      toast.error("Count not saved", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer open onClose={onClose} title={`Count ${location.name}`}
            footer={<>
              <span className="amn-spacer" />
              <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
              <Button variant="primary" onClick={save} loading={busy} disabled={!filled.length || bad}>
                Save {filled.length || ""} count{filled.length === 1 ? "" : "s"}
              </Button>
            </>}>
      {rows.length === 0 ? (
        <p className="amn-note">No par levels at this location yet, so there is nothing to count against.</p>
      ) : (
        <div className="amn-stack" style={{ gap: 12 }}>
          <Field label="Counted by" hint="Optional">
            <Input value={by} maxLength={80} onChange={e => setBy(e.target.value)} />
          </Field>
          {rows.map(p => {
            const v = values[p.item_id] ?? "";
            return (
              <Field key={p.item_id} label={p.item_name}
                     hint={`Par ${p.par_qty === null ? "not set" : qtyText(p.par_qty)}${p.on_hand !== null ? `, last count ${qtyText(p.on_hand)}` : ""}`}
                     error={v.trim() && !isCount(v) ? "A number" : undefined}>
                <Input inputMode="decimal" value={v} placeholder="Not counted"
                       onChange={e => setValues(prev => ({ ...prev, [p.item_id]: e.target.value }))} />
              </Field>
            );
          })}
        </div>
      )}
    </Drawer>
  );
}
