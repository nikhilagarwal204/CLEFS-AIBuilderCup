"use client";

/* Settings > Rooms: the room register (hotel_rooms) by floor.
   Floors route requests to the right floor tablet and housekeeping team,
   bedrooms set the Tourism Dirham units, and the service state keeps an
   out-of-order room out of occupancy. A hotel with no rooms here is
   "unregistered": any room number is accepted and its floor is read from
   the number, which is fine for a pilot and wrong for a tower with a
   Penthouse floor, so this page makes registering the list a two-minute job
   (add a range, or drop the PMS room list as CSV). */

import { useMemo, useState } from "react";
import { LuPlus, LuQrCode } from "react-icons/lu";
import { api, hotelPath, useApi } from "../../../../../lib/api";
import { useSession } from "../../../../../lib/session";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, EmptyState, Input, Select, StatTile, Table, useToast, type Column } from "../../../../../ui";
import { errorText } from "../../../../../ingest/helpers";
import { floorSort, roomSort, SERVICE_LABEL, type Room, type RoomInput } from "./rooms";
import { RoomEditor } from "./RoomEditor";
import { RoomsCsvImport } from "./RoomsCsvImport";

const CHUNK = 200;
const NO_FLOOR = "\u0000none";

const rmsStyles = `
.rms-note { font-size: 0.8rem; color: var(--clefs-ink-muted); line-height: 1.5; margin: 0; }
.rms-bad {
  border: 1px solid rgba(var(--cx-bad-rgb), 0.35); background: rgba(var(--cx-bad-rgb), 0.08);
  border-radius: var(--cx-radius-sm); padding: 10px 12px; font-size: 0.84rem; color: var(--clefs-ink);
}
.rms-errors { margin: 0; padding-left: 18px; font-size: 0.8rem; color: var(--clefs-ink-muted); }
.rms-floors { display: flex; flex-wrap: wrap; gap: 8px; }
.rms-floor {
  display: flex; flex-direction: column; gap: 2px; min-width: 96px; padding: 10px 12px; text-align: left;
  border: 1px solid var(--clefs-line-soft); border-radius: var(--cx-radius-sm); background: var(--cx-field);
  color: var(--clefs-ink); font: inherit; cursor: pointer;
}
.rms-floor[aria-pressed="true"] { border-color: var(--clefs-gold-500); background: rgba(var(--clefs-gold-rgb), 0.08); }
.rms-floor__name { font-weight: 600; }
.rms-floor__sub { font-size: 0.74rem; color: var(--clefs-ink-muted); }
.rms-filters { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.rms-filters > * { flex: 1 1 180px; max-width: 280px; }
.rms-num { font-weight: 600; font-variant-numeric: tabular-nums; }
`;

export default function RoomsSettingsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const canView = can(["core.settings.view", "service.view"]);
  const canWrite = can("core.settings.manage");
  const res = useApi<Room[]>(slug && canView ? hotelPath(slug, "/rooms") : null, [slug]);
  const rooms = useMemo(() => [...(res.data ?? [])].sort(roomSort), [res.data]);
  const [floor, setFloor] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [show, setShow] = useState<"active" | "all">("active");
  const [editing, setEditing] = useState<Room | "new" | null>(null);
  const [saving, setSaving] = useState(false);

  const existing = useMemo(() => new Set(rooms.map(r => r.room_number)), [rooms]);
  const floors = useMemo(() => {
    const m = new Map<string, Room[]>();
    for (const r of rooms) {
      if (!r.is_active) continue;
      const k = r.floor ?? NO_FLOOR;
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return [...m].sort(([a], [b]) => (a === NO_FLOOR ? 1 : b === NO_FLOOR ? -1 : floorSort(a, b)));
  }, [rooms]);
  const active = rooms.filter(r => r.is_active);
  const ooo = active.filter(r => r.service_state && r.service_state !== "in_service").length;
  const bedrooms = active.reduce((n, r) => n + (r.bedrooms ?? 1), 0);
  const needle = q.trim().toLowerCase();
  const list = rooms.filter(r => (show === "all" || r.is_active)
    && (floor === null || (r.floor ?? NO_FLOOR) === floor)
    && (!needle || r.room_number.toLowerCase().includes(needle) || (r.wing ?? "").toLowerCase().includes(needle)));

  const save = async (items: RoomInput[]): Promise<boolean> => {
    setSaving(true);
    try {
      let n = 0;
      // Chunked so a 900-room tower is several short requests, not one long one.
      for (let i = 0; i < items.length; i += CHUNK) {
        const out = await api.put<{ ok: boolean; count: number }>(hotelPath(slug, "/rooms"), { rooms: items.slice(i, i + CHUNK) });
        n += out.count ?? 0;
      }
      toast.success(n === 1 ? "Room saved" : `${n} rooms saved`);
      setEditing(null);
      res.reload();
      return true;
    } catch (err) {
      toast.error("Couldn't save rooms", errorText(err));
      res.reload();
      return false;
    } finally {
      setSaving(false);
    }
  };

  const columns: Column<Room>[] = [
    { key: "n", header: "Room", render: r => <span className="rms-num">{r.room_number}</span> },
    {
      key: "floor", header: "Floor",
      render: r => (
        <span>{r.floor ?? "-"} {r.floor_inferred && <Badge tone="neutral" title="Read from the room number">auto</Badge>}</span>
      ),
    },
    { key: "wing", header: "Wing", hideOnMobile: true, render: r => r.wing || "-" },
    { key: "type", header: "Type", hideOnMobile: true, render: r => r.room_type_code || "-" },
    { key: "beds", header: "Bedrooms", align: "right", render: r => <span className="cx-num">{r.bedrooms ?? 1}</span> },
    { key: "ext", header: "Ext.", hideOnMobile: true, render: r => r.extension || "-" },
    {
      key: "state", header: "State",
      render: r => (!r.is_active
        ? <Badge tone="neutral">Removed</Badge>
        : r.service_state && r.service_state !== "in_service"
          ? <Badge tone="warn" title={r.ooo_reason ?? undefined}>{SERVICE_LABEL[r.service_state]}</Badge>
          : <Badge tone="ok">{SERVICE_LABEL.in_service}</Badge>),
    },
    {
      key: "x", header: <span className="cx-sr">Actions</span>, align: "right",
      render: r => canWrite ? (
        // The row itself opens the editor: keep these clicks from reaching it.
        <div className="cx-row" style={{ justifyContent: "flex-end" }}
             onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
          <Button size="sm" variant="ghost" onClick={() => setEditing(r)}>Edit</Button>
          <Button size="sm" variant="ghost" disabled={saving}
                  onClick={() => void save([{ room_number: r.room_number, is_active: !r.is_active }])}>
            {r.is_active ? "Remove" : "Restore"}
          </Button>
        </div>
      ) : null,
    },
  ];

  if (!canView) {
    return <EmptyState title="Rooms are managed by the owner or GM" body="Ask them for access to the room register." />;
  }

  return (
    <div className="cx-stack" style={{ marginTop: 18 }}>
      <style dangerouslySetInnerHTML={{ __html: rmsStyles }} />
      <div className="cx-grid cx-grid--4">
        <StatTile label="Rooms" value={active.length} loading={res.loading && !res.data}
                  sub={rooms.length > active.length ? `${rooms.length - active.length} removed` : undefined} />
        <StatTile label="Floors" value={floors.filter(([k]) => k !== NO_FLOOR).length} loading={res.loading && !res.data} />
        <StatTile label="Out of order" value={ooo} loading={res.loading && !res.data} />
        <StatTile label="Bedrooms" value={bedrooms} loading={res.loading && !res.data} sub="Tourism Dirham units per night" />
      </div>

      {res.error && <div className="rms-bad" role="alert">{res.error.message}</div>}

      {!res.loading && !res.error && rooms.length === 0 ? (
        <Card title="No rooms registered yet">
          <EmptyState title="Any room number works for now"
                      body="Until you register rooms, guests and staff can use any room number and the floor is read from it (502 is floor 5). Register them to name floors, count bedrooms and mark rooms out of order."
                      action={canWrite ? <Button icon={<LuPlus aria-hidden="true" />} onClick={() => setEditing("new")}>Add rooms</Button> : undefined} />
        </Card>
      ) : (
        <>
          <Card title="Floors" subtitle="Tap a floor to list its rooms.">
            <div className="rms-floors">
              {floors.map(([k, list]) => {
                const bad = list.filter(r => r.service_state && r.service_state !== "in_service").length;
                return (
                  <button key={k} type="button" className="rms-floor" aria-pressed={floor === k}
                          onClick={() => setFloor(f => (f === k ? null : k))}>
                    <span className="rms-floor__name">{k === NO_FLOOR ? "No floor" : `Floor ${k}`}</span>
                    <span className="rms-floor__sub">{list.length} rooms{bad ? `, ${bad} out` : ""}</span>
                  </button>
                );
              })}
            </div>
          </Card>

          <Card title="Rooms"
                actions={canWrite ? (
                  <>
                    {/* One printable QR card per room: guests scan it to reach the assistant (05 spec 1). */}
                    <Button size="sm" variant="ghost" icon={<LuQrCode aria-hidden="true" />}
                            href={`/app/${encodeURIComponent(slug)}/settings/rooms/qr`}>Print QR cards</Button>
                    <Button size="sm" icon={<LuPlus aria-hidden="true" />} onClick={() => setEditing("new")}>Add rooms</Button>
                  </>
                ) : undefined}>
            <div className="rms-filters">
              <Input placeholder="Find a room or wing" value={q} onChange={e => setQ(e.target.value)} aria-label="Find a room" />
              <Select aria-label="Which rooms" value={show} onChange={e => setShow(e.target.value as "active" | "all")}
                      options={[{ value: "active", label: "Active rooms" }, { value: "all", label: "Include removed" }]} />
            </div>
            <Table columns={columns} rows={list} rowKey={r => r.room_number} loading={res.loading && !res.data}
                   onRowClick={canWrite ? r => setEditing(r) : undefined} caption="Rooms"
                   empty={<p className="rms-note">No rooms match.</p>} />
          </Card>
        </>
      )}

      {canWrite && (
        <Card title="Import from a spreadsheet" subtitle="Export the room list from your PMS and drop it here. Existing rooms are updated, never deleted.">
          <RoomsCsvImport existing={existing} saving={saving} onImport={save} />
        </Card>
      )}

      {editing !== null && (
        <RoomEditor key={editing === "new" ? "new" : editing.room_number} open
                    room={editing === "new" ? null : editing} existing={existing} saving={saving}
                    onClose={() => setEditing(null)} onSave={items => void save(items)} />
      )}
    </div>
  );
}
