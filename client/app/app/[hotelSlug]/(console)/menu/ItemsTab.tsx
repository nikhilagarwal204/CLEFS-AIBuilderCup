"use client";

/* Items: the dishes and drinks. Filterable by outlet and by a text search
   over both languages, because front-desk staff taking a phone order often
   only remember half an Arabic name. Prices print through the hotel
   formatter (format.ts), never a hard-coded symbol. */

import { useMemo, useState } from "react";
import { apiUrl, hotelPath } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import {
  Badge, Button, Card, EmptyState, Field, Input, Select, Table, useToast, type Column,
} from "../../../../ui";
import ItemEditor from "./ItemEditor";
import { daysText, dietaryLabel } from "./lib";
import type { MenuData, MenuItem } from "./types";

export default function ItemsTab({ data }: { data: MenuData }) {
  const fmt = useFormat();
  const toast = useToast();
  const [outletId, setOutletId] = useState("");
  const [q, setQ] = useState("");
  const [showHidden, setShowHidden] = useState(true);
  const [editing, setEditing] = useState<MenuItem | "new" | null>(null);

  const outletName = useMemo(() => {
    const m = new Map(data.outlets.map(o => [o.id, o.name_en]));
    return (id: number) => m.get(id) ?? "Unknown outlet";
  }, [data.outlets]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return data.items
      .filter(i => !outletId || String(i.outlet_id) === outletId)
      .filter(i => showHidden || i.active)
      .filter(i => !needle || [i.name_en, i.name_ar, i.sku, i.category, i.category_ar]
        .some(v => (v ?? "").toLowerCase().includes(needle)))
      .sort((a, b) => (a.category ?? "").localeCompare(b.category ?? "") || a.sort - b.sort || a.name_en.localeCompare(b.name_en));
  }, [data.items, outletId, q, showHidden]);

  const columns: Column<MenuItem>[] = [
    {
      key: "name", header: "Item",
      render: i => (
        <div className="cx-row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
          {i.image_file_id && (
            // eslint-disable-next-line @next/next/no-img-element -- authenticated API file, not a static asset
            <img className="ups-thumb" alt="" loading="lazy"
                 src={apiUrl(hotelPath(data.slug, `/files/${encodeURIComponent(i.image_file_id)}`))}
                 onError={e => { e.currentTarget.style.visibility = "hidden"; }} />
          )}
          <div style={{ minWidth: 0 }}>
            <div className="ups-item-name">{i.name_en}</div>
            {i.name_ar && <div className="ups-item-ar" lang="ar">{i.name_ar}</div>}
            <div className="ups-sub">{[i.category, i.sku].filter(Boolean).join(" · ") || "No category"}</div>
          </div>
        </div>
      ),
    },
    { key: "outlet", header: "Outlet", hideOnMobile: !data.outlets.length, render: i => outletName(i.outlet_id) },
    {
      key: "price", header: "Price", align: "right",
      render: i => <span className="ups-price">{fmt.money(i.price_minor, { minor: true, currency: i.currency })}</span>,
    },
    {
      key: "when", header: "Available",
      render: i => (
        <span className="ups-sub">
          {i.available_from && i.available_to ? `${i.available_from}-${i.available_to}` : "Outlet hours"}
          {i.days_mask !== 127 && ` · ${daysText(i.days_mask)}`}
        </span>
      ),
    },
    {
      key: "tags", header: "Tags",
      render: i => (
        <div className="ups-tags">
          {!i.active && <Badge>Hidden</Badge>}
          {i.alcohol && <Badge tone="warn">Alcohol</Badge>}
          {i.dietary.map(d => <Badge key={d} tone="neutral">{dietaryLabel(d)}</Badge>)}
          {i.prep_min ? <Badge tone="info">{i.prep_min} min</Badge> : null}
        </div>
      ),
    },
  ];

  const noOutlets = !data.loading && data.outlets.length === 0;

  return (
    <Card
      title="Items"
      subtitle="Names and descriptions in English and Arabic. Guests see the language they chose."
      actions={data.canManage && (
        <Button variant="primary" size="sm" disabled={noOutlets} onClick={() => setEditing("new")}>Add item</Button>
      )}
    >
      <div className="ups-toolbar">
        <Field label="Outlet">
          <Select value={outletId} onChange={e => setOutletId(e.target.value)} placeholder="All outlets"
                  options={data.outlets.map(o => ({ value: String(o.id), label: o.name_en }))} />
        </Field>
        <Field label="Search">
          <Input type="search" value={q} onChange={e => setQ(e.target.value)} placeholder="Name, SKU or category" />
        </Field>
        <label className="ups-check" style={{ paddingBottom: 10 }}>
          <input type="checkbox" checked={showHidden} onChange={e => setShowHidden(e.target.checked)} />
          Show hidden items
        </label>
      </div>
      <Table
        columns={columns}
        rows={rows}
        rowKey={i => i.id}
        loading={data.loading}
        onRowClick={data.canManage ? i => setEditing(i) : undefined}
        empty={noOutlets ? (
          <EmptyState title="Add an outlet first" body="Items belong to an outlet such as in-room dining. Add one in the Outlets tab, or import a CSV." />
        ) : data.items.length === 0 ? (
          <EmptyState
            title="No items yet"
            body="Add dishes one by one, or import your whole menu from a spreadsheet in the Import tab."
            action={data.canManage && <Button variant="primary" onClick={() => setEditing("new")}>Add item</Button>}
          />
        ) : "No items match these filters."}
      />
      {editing && (
        <ItemEditor
          data={data}
          item={editing === "new" ? null : editing}
          defaultOutletId={outletId ? Number(outletId) : data.outlets[0]?.id}
          onClose={() => setEditing(null)}
          onSaved={msg => { toast.success(msg); setEditing(null); data.reloadItems(); }}
        />
      )}
    </Card>
  );
}
