"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Par levels (02 spec 5.3 par/): how much of each item every location
   should hold, what it held at the last count, and what the weekly
   recompute suggests from on-the-books rooms plus pickup.

   A par typed by a person is pinned: recompute leaves it alone and only
   refreshes the suggestion beside it, so the manager sees the gap and
   decides. Counts are open to floor staff (amenities.issue), because the
   person at the closet is the one who can count it.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { LuClipboardCheck, LuPin, LuPlus, LuRefreshCw } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Select, Table, useToast, type Column } from "../../../../../ui";
import AmenitiesShell, { AmnEmpty, PERM_ISSUE, PERM_MANAGE, PERM_VIEW } from "./AmenitiesShell";
import CountDrawer from "./CountDrawer";
import LocationsCard from "./LocationsCard";
import ParEditor from "./ParEditor";
import { LOCATION_KIND_LABELS, num, qtyText } from "./lib";
import type { Item, Location, Par } from "./types";

export default function ParConsole() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = can(PERM_MANAGE);
  const canCount = can([PERM_ISSUE, PERM_MANAGE]);
  const [version, setVersion] = useState(0);
  const [loc, setLoc] = useState("");
  const [editing, setEditing] = useState<Par | "new" | null>(null);
  const [counting, setCounting] = useState<Location | null>(null);
  const [recomputing, setRecomputing] = useState(false);
  const refresh = () => setVersion(v => v + 1);

  const locations = useApi<Location[]>(hotelPath(slug, "/amenities/locations"), [slug, version]);
  const par = useApi<Par[]>(hotelPath(slug, `/amenities/par${loc ? `?location_id=${loc}` : ""}`), [slug, version]);
  const items = useApi<Item[]>(canManage ? hotelPath(slug, "/amenities/items") : null, [slug]);
  const activeLocs = useMemo(() => (locations.data ?? []).filter(l => l.active), [locations.data]);

  const recompute = async () => {
    setRecomputing(true);
    try {
      const r = await api.post<{ updated: number; pinned: number }>(hotelPath(slug, "/amenities/par/recompute"));
      toast.success("Par levels recomputed", `${r.updated} updated, ${r.pinned} pinned kept as set.`);
      refresh();
    } catch (err) {
      toast.error("Recompute failed", err instanceof ApiError ? err.message : undefined);
    } finally {
      setRecomputing(false);
    }
  };

  const cols: Column<Par>[] = [
    { key: "loc", header: "Location", render: p => (
      <span className="amn-rowtitle">
        <span>{p.location_name}</span>
        <small>{LOCATION_KIND_LABELS[p.location_kind] ?? p.location_kind}{p.floor ? `, floor ${p.floor}` : ""}</small>
      </span>
    ) },
    { key: "item", header: "Item", render: p => p.item_name },
    { key: "on", header: "On hand", align: "right", render: p => <OnHand p={p} /> },
    { key: "par", header: "Par", align: "right", render: p => (
      <span className="amn-mono" style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
        {p.pinned && <LuPin aria-label="Set by hand" size={12} />}
        {p.par_qty === null ? "Not set" : qtyText(p.par_qty)}
      </span>
    ) },
    { key: "sug", header: "Suggested", align: "right", hideOnMobile: true, render: p => {
      const s = p.computed?.suggested_par;
      return s === undefined || s === null ? "" : <span className="amn-mono">{qtyText(s)}</span>;
    } },
    { key: "counted", header: "Last count", hideOnMobile: true,
      render: p => (p.last_counted_at ? fmt.relative(p.last_counted_at) : "Never") },
    ...(canManage ? [{
      key: "act", header: "", align: "right" as const,
      render: (p: Par) => <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>Edit</Button>,
    }] : []),
  ];

  const actions = (
    <>
      {canManage && (
        <Button size="sm" variant="ghost" icon={<LuRefreshCw />} loading={recomputing} onClick={recompute}>Recompute</Button>
      )}
      {canManage && (
        <Button size="sm" variant="primary" icon={<LuPlus />} onClick={() => setEditing("new")}
                disabled={!activeLocs.length}>Add par</Button>
      )}
    </>
  );

  const rows = par.data ?? [];
  const below = rows.filter(p => p.on_hand !== null && p.par_qty !== null && num(p.on_hand) < num(p.par_qty)).length;
  const countLoc = activeLocs.find(l => String(l.id) === loc) ?? null;

  return (
    <AmenitiesShell active="par" need={[PERM_VIEW, PERM_ISSUE, PERM_MANAGE]} title="Par levels"
                    subtitle="Target stock per location, the last count and the suggested par from forecast rooms."
                    actions={actions}>
      <div className="amn-stack">
        <Card>
          <div className="amn-toolbar">
            <Select aria-label="Location" value={loc} onChange={e => setLoc(e.target.value)} style={{ width: "auto" }}>
              <option value="">All locations</option>
              {activeLocs.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </Select>
            {below > 0 && <Badge tone="warn">{below} below par at last count</Badge>}
            <span className="amn-spacer" />
            {canCount && countLoc && (
              <Button icon={<LuClipboardCheck />} onClick={() => setCounting(countLoc)}>Count {countLoc.name}</Button>
            )}
          </div>
          {!countLoc && canCount && activeLocs.length > 0 && (
            <p className="amn-note" style={{ marginTop: 0 }}>Pick a location to record a stock count for it.</p>
          )}
          {par.error ? (
            <p className="amn-note" role="alert">{par.error.message}</p>
          ) : !par.loading && rows.length === 0 ? (
            <AmnEmpty title="No par levels yet"
                      body={activeLocs.length
                        ? "Add a par for each item a location holds, then run Recompute to get suggestions."
                        : "Add the main store and floor closets below first."} />
          ) : (
            <Table columns={cols} rows={rows} rowKey={p => `${p.location_id}-${p.item_id}`} loading={par.loading && !par.data}
                   caption="Par levels" />
          )}
        </Card>
        <LocationsCard locations={locations.data ?? []} loading={locations.loading && !locations.data}
                       canManage={canManage} onChanged={refresh} />
      </div>
      {editing !== null && (
        <ParEditor row={editing === "new" ? null : editing} locations={activeLocs} items={items.data ?? []}
                   onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />
      )}
      {counting && (
        <CountDrawer location={counting} rows={rows.filter(p => p.location_id === counting.id)}
                     onClose={() => setCounting(null)} onSaved={() => { setCounting(null); refresh(); }} />
      )}
    </AmenitiesShell>
  );
}

function OnHand({ p }: { p: Par }) {
  if (p.on_hand === null) return <span className="amn-muted">No count</span>;
  const low = p.par_qty !== null && num(p.on_hand) < num(p.par_qty);
  return (
    <span className="amn-mono" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
      {qtyText(p.on_hand)}
      {low && <Badge tone="warn">Below par</Badge>}
    </span>
  );
}
