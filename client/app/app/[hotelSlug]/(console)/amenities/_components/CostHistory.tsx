"use client";

/* Unit cost history for one item. Each day is valued at the latest cost on
   or before it, so a supplier price change entered with its effective date
   re-values only the days after it (the server re-settles those days).
   The list needs amenities.view; a manager without it still edits the
   item, the history just stays hidden. */

import { useState } from "react";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Field, Input, useToast } from "../../../../../ui";
import { PERM_VIEW } from "./AmenitiesShell";

interface CostRow {
  id: number;
  effective_from: string;
  unit_cost: string | null;
  excise: string | null;
  source: string;
  invoice_ref: string | null;
}

const isMoney = (s: string) => /^\d{1,7}(\.\d{1,3})?$/.test(s.trim());

export default function CostHistory({ itemId }: { itemId: number }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const [from, setFrom] = useState(() => fmt.dayKey(new Date()));
  const [cost, setCost] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const costs = useApi<CostRow[]>(can(PERM_VIEW) ? hotelPath(slug, `/amenities/items/${itemId}/costs`) : null, [slug, version]);

  if (!can(PERM_VIEW)) return null;

  const add = async () => {
    if (!isMoney(cost) || !from) return;
    setBusy(true);
    try {
      await api.post(hotelPath(slug, `/amenities/items/${itemId}/costs`), {
        effective_from: from, unit_cost: cost.trim(), invoice_ref: ref.trim() || null,
        source: ref.trim() ? "SUPPLIER_INVOICE" : "MANUAL",
      });
      toast.success("Cost added", `Days from ${from} are being re-valued.`);
      setCost("");
      setRef("");
      setVersion(v => v + 1);
    } catch (err) {
      toast.error("Cost not added", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={{ marginTop: 20 }} aria-label="Cost history">
      <h3 className="cx-card__title" style={{ marginBottom: 8 }}>Cost history</h3>
      {costs.data && costs.data.length > 0 ? (
        <ul className="amn-recent">
          {costs.data.slice(0, 8).map(c => (
            <li key={c.id}>
              <span>From {c.effective_from}{c.invoice_ref ? `, invoice ${c.invoice_ref}` : ""}</span>
              <span className="amn-mono">{fmt.money(c.unit_cost, { maximumFractionDigits: 3 })}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="amn-muted">{costs.loading ? "Loading..." : "No cost history yet."}</p>
      )}
      <div className="amn-form" style={{ marginTop: 12 }}>
        <Field label="New cost from">
          <Input type="date" value={from} onChange={e => setFrom(e.target.value)} />
        </Field>
        <Field label="Unit cost incl. excise" error={cost && !isMoney(cost) ? "An amount like 1.25" : undefined}>
          <Input inputMode="decimal" value={cost} placeholder="0.00" onChange={e => setCost(e.target.value)} />
        </Field>
        <Field label="Invoice number" hint="Optional">
          <Input value={ref} maxLength={80} onChange={e => setRef(e.target.value)} />
        </Field>
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <Button onClick={add} loading={busy} disabled={!isMoney(cost) || !from}>Add cost</Button>
        </div>
      </div>
    </section>
  );
}
