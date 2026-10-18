"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Amenity catalog (02 spec 5.3 catalog/): every item the leakage maths
   runs on, with its basis, standard quantity and unit cost including
   excise. UAE hotels start from a seeded default list flagged "needs
   review": those costs and standards are market defaults, so the first job
   is to confirm or correct each row. Readers (and floor staff) see the
   list; editing needs amenities.manage.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useMemo, useState } from "react";
import { LuPlus, LuSparkles } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Input, Select, Table, useToast, type Column } from "../../../../../ui";
import AmenitiesShell, { AmnEmpty, amnHref, PERM_ISSUE, PERM_MANAGE, PERM_VIEW } from "./AmenitiesShell";
import ItemEditor from "./ItemEditor";
import { BASIS_LABELS, CATEGORY_LABELS, categoryLabel, qtyText } from "./lib";
import type { Item } from "./types";

export default function CatalogConsole() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = can(PERM_MANAGE);
  const canView = can(PERM_VIEW);
  const [version, setVersion] = useState(0);
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState("");
  const [show, setShow] = useState<"all" | "review" | "inactive">("all");
  const [editing, setEditing] = useState<Item | "new" | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);

  const items = useApi<Item[]>(hotelPath(slug, "/amenities/items?active_only=false"), [slug, version]);
  const refresh = () => setVersion(v => v + 1);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items.data ?? []).filter(i =>
      (!cat || i.category === cat)
      && (show === "all" ? i.active : show === "review" ? i.needs_review && i.active : !i.active)
      && (!q || i.name_en.toLowerCase().includes(q) || (i.name_ar ?? "").includes(q) || i.key.toLowerCase().includes(q)
        || (i.sku ?? "").toLowerCase().includes(q) || i.aliases.some(a => a.toLowerCase().includes(q))));
  }, [items.data, query, cat, show]);
  const reviewCount = (items.data ?? []).filter(i => i.needs_review && i.active).length;

  const seed = async () => {
    setSeeding(true);
    try {
      const r = await api.post<{ seeded: number; already: boolean }>(hotelPath(slug, "/amenities/items/seed-defaults"), {});
      toast.success(r.already ? "Defaults already loaded" : `${r.seeded} default items added`,
        "Check each cost and standard, then confirm it.");
      refresh();
    } catch (err) {
      toast.error("Could not load defaults", err instanceof ApiError ? err.message : undefined);
    } finally {
      setSeeding(false);
    }
  };

  // Confirming a seeded row keeps its values and clears the review flag.
  const confirm = async (item: Item) => {
    setConfirming(item.id);
    try {
      const row = await api.patch<Item>(hotelPath(slug, `/amenities/items/${item.id}`), { needs_review: false });
      items.setData(prev => (prev ?? []).map(i => (i.id === row.id ? row : i)));
    } catch (err) {
      toast.error("Could not confirm", err instanceof ApiError ? err.message : undefined);
    } finally {
      setConfirming(null);
    }
  };

  const cols: Column<Item>[] = [
    { key: "name", header: "Item", render: i => (
      <span className="amn-rowtitle">
        {canView ? <Link className="amn-link" href={amnHref(slug, `/items/${i.id}`)}>{i.name_en}</Link> : <b>{i.name_en}</b>}
        <small>{i.name_ar ? <span lang="ar" dir="rtl">{i.name_ar}</span> : i.key}</small>
      </span>
    ) },
    { key: "cat", header: "Category", hideOnMobile: true, render: i => categoryLabel(i.category) },
    { key: "std", header: "Standard", render: i => (
      i.basis === "ON_REQUEST" ? "On request" : `${qtyText(i.std_qty)} ${BASIS_LABELS[i.basis]?.toLowerCase() ?? ""}`
    ) },
    { key: "cost", header: "Unit cost", align: "right", render: i => (
      <span className="amn-rowtitle" style={{ textAlign: "right" }}>
        <span className="amn-mono">{i.unit_cost ? fmt.money(i.unit_cost, { maximumFractionDigits: 2 }) : "Not set"}</span>
        {i.excise_category !== "NONE" && <small>incl. excise</small>}
      </span>
    ) },
    { key: "flags", header: "", render: i => (
      <span className="amn-chips">
        {i.needs_review && <Badge tone="gold">Default, review</Badge>}
        {i.is_single_use_plastic && <Badge tone="warn" title={i.sup_ban_ref ?? undefined}>Single-use plastic</Badge>}
        {i.chargeable && <Badge tone="info">Chargeable</Badge>}
        {!i.active && <Badge>Inactive</Badge>}
      </span>
    ) },
    ...(canManage ? [{
      key: "act", header: "", align: "right" as const, render: (i: Item) => (
        <span className="amn-chips" style={{ justifyContent: "flex-end" }}>
          {i.needs_review && (
            <Button size="sm" variant="secondary" loading={confirming === i.id} onClick={() => confirm(i)}>Confirm</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setEditing(i)}>Edit</Button>
        </span>
      ),
    }] : []),
  ];

  const actions = canManage ? (
    <Button size="sm" variant="primary" icon={<LuPlus />} onClick={() => setEditing("new")}>Add item</Button>
  ) : undefined;
  const empty = (items.data ?? []).length === 0;

  return (
    <AmenitiesShell active="catalog" need={[PERM_VIEW, PERM_ISSUE, PERM_MANAGE]} title="Amenity catalog"
                    subtitle="Items, standards per room and unit costs including excise." actions={actions}>
      {reviewCount > 0 && canManage && (
        <div className="amn-banner" role="status">
          <LuSparkles aria-hidden="true" />
          <span>
            {reviewCount} item{reviewCount === 1 ? " uses" : "s use"} default costs and standards. Correct anything that
            differs from your supplier prices and room set-up, then confirm. Leakage figures use these values.
          </span>
        </div>
      )}
      <Card>
        <div className="amn-toolbar">
          <Input type="search" value={query} placeholder="Search name, key, SKU or alias" aria-label="Search the catalog"
                 onChange={e => setQuery(e.target.value)} style={{ flex: "1 1 220px" }} />
          <Select aria-label="Category" value={cat} onChange={e => setCat(e.target.value)} style={{ width: "auto" }}>
            <option value="">All categories</option>
            {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Select aria-label="Show" value={show} onChange={e => setShow(e.target.value as typeof show)} style={{ width: "auto" }}>
            <option value="all">Active</option>
            <option value="review">Needs review</option>
            <option value="inactive">Inactive</option>
          </Select>
        </div>
        {items.error ? (
          <p className="amn-note" role="alert">{items.error.message}</p>
        ) : empty && !items.loading ? (
          <AmnEmpty title="No items yet"
                    body="Add the amenities you stock, or start from the UAE default list and adjust it."
                    action={canManage ? <Button variant="primary" onClick={seed} loading={seeding}>Load UAE defaults</Button> : undefined} />
        ) : (
          <Table columns={cols} rows={rows} rowKey={i => i.id} loading={items.loading && !items.data}
                 caption="Amenity catalog" empty="No item matches these filters." />
        )}
      </Card>
      {editing !== null && (
        <ItemEditor item={editing === "new" ? null : editing} onClose={() => setEditing(null)}
                    onSaved={() => { setEditing(null); refresh(); }} />
      )}
    </AmenitiesShell>
  );
}
