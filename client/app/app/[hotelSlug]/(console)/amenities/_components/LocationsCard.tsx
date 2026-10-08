"use client";

/* Stock locations (main store, floor closets, trolleys, minibars). Par
   levels and counts hang off these; a floor closet's floor is what par
   recompute uses to share the hotel's forecast use across floors. */

import { useState } from "react";
import { LuTrash2 } from "react-icons/lu";
import { ApiError, api, hotelPath } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Field, Input, Select, Table, useToast, type Column } from "../../../../../ui";
import { floorLabel, LOCATION_KIND_LABELS } from "./lib";
import type { Location, LocationKind } from "./types";

export default function LocationsCard({ locations, loading, canManage, onChanged }: {
  locations: Location[];
  loading: boolean;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [kind, setKind] = useState<LocationKind>("FLOOR_CLOSET");
  const [name, setName] = useState("");
  const [floor, setFloor] = useState("");
  const [busy, setBusy] = useState<number | "add" | null>(null);

  const add = async () => {
    if (!name.trim()) return;
    setBusy("add");
    try {
      await api.post(hotelPath(slug, "/amenities/locations"), { kind, name: name.trim(), floor: floor.trim() || null });
      setName("");
      setFloor("");
      onChanged();
    } catch (err) {
      toast.error("Location not added", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (l: Location) => {
    setBusy(l.id);
    try {
      const r = await api.del<{ deleted: boolean }>(hotelPath(slug, `/amenities/locations/${l.id}`));
      toast.success(r.deleted ? "Location removed" : "Location deactivated",
        r.deleted ? undefined : "It has counts or par rows, so it is kept for history.");
      onChanged();
    } catch (err) {
      toast.error("Could not remove", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(null);
    }
  };

  const cols: Column<Location>[] = [
    { key: "name", header: "Location", render: l => <b>{l.name}</b> },
    { key: "kind", header: "Kind", render: l => LOCATION_KIND_LABELS[l.kind] ?? l.kind },
    { key: "floor", header: "Floor", render: l => (l.floor ? floorLabel(l.floor) : "") },
    { key: "state", header: "", render: l => (l.active ? null : <Badge>Inactive</Badge>) },
    ...(canManage ? [{
      key: "act", header: "", align: "right" as const, render: (l: Location) => (
        <Button size="sm" variant="ghost" icon={<LuTrash2 />} aria-label={`Remove ${l.name}`}
                loading={busy === l.id} onClick={() => remove(l)} />
      ),
    }] : []),
  ];

  return (
    <Card title="Locations" subtitle="Where stock is kept and counted">
      <Table columns={cols} rows={locations} rowKey={l => l.id} loading={loading} caption="Stock locations"
             empty="No locations yet. Add the main store and each floor closet." />
      {canManage && (
        <div className="amn-toolbar" style={{ marginTop: 14, marginBottom: 0 }}>
          <Field label="Kind">
            <Select value={kind} onChange={e => setKind(e.target.value as LocationKind)}>
              {Object.entries(LOCATION_KIND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
          <Field label="Name">
            <Input value={name} maxLength={80} placeholder="e.g. Floor 6 closet" onChange={e => setName(e.target.value)} />
          </Field>
          <Field label="Floor">
            <Input value={floor} maxLength={20} inputMode="numeric" placeholder="6" onChange={e => setFloor(e.target.value)}
                   style={{ width: 90 }} />
          </Field>
          <Button onClick={add} loading={busy === "add"} disabled={!name.trim()}>Add location</Button>
        </div>
      )}
    </Card>
  );
}
