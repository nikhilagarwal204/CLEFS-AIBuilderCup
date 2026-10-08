"use client";

/* ═══════════════════════════════════════════════════════════════════════
   In-house roster: the legacy guest_rooms map (one JSON blob per room that
   the concierge reads for "who is in this room"), with the old
   StaffDashboard GuestModal moved here as RoomEditor. Phone and email are
   never typed into the blob any more: the editor's identity section posts
   them, with the guest's consent ticks, to the identity API, which hashes
   them and keeps only a masked copy on the roster.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Input, Table, type Column } from "../../../../../ui";
import { gstApi, PERM } from "../lib";
import type { Roster, RosterRoom } from "../types";
import RoomEditor from "./RoomEditor";

function roomSort(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

export default function RosterPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const res = useApi<Roster>(gstApi(slug), [slug]);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<{ room: string | null } | null>(null);

  const rows = useMemo(() => {
    const all = Object.entries(res.data ?? {}).map(([room, data]) => ({ ...data, roomNumber: data.roomNumber || room }));
    const q = filter.trim().toLowerCase();
    return all
      .filter(r => !q || r.roomNumber.toLowerCase().includes(q) || (r.guests ?? []).some(g => g.name?.toLowerCase().includes(q)))
      .sort((a, b) => roomSort(a.roomNumber, b.roomNumber));
  }, [res.data, filter]);

  const cols: Column<RosterRoom>[] = [
    { key: "room", header: "Room", render: r => <strong>{r.roomNumber}</strong> },
    { key: "lead", header: "Guests", render: r => {
      const g = r.guests ?? [];
      return g.length ? <span>{g[0].name || "Guest"}{g.length > 1 ? <span className="gst-muted"> +{g.length - 1}</span> : null}</span>
        : <span className="gst-faint">-</span>;
    } },
    { key: "stay", header: "Stay", render: r => (
      <span className="gst-small">{r.checkIn ? fmt.date(`${r.checkIn}T12:00:00Z`) : "?"} to {r.checkOut ? fmt.date(`${r.checkOut}T12:00:00Z`) : "?"}</span>
    ) },
    { key: "type", header: "Type", hideOnMobile: true, render: r => (r.stayType ? <Badge>{r.stayType}</Badge> : "-") },
    { key: "tags", header: "Preferences", hideOnMobile: true, render: r => (
      <span className="gst-small">{[...(r.preferences?.tags ?? []), ...(r.occasions ?? [])].slice(0, 3).join(", ") || "-"}</span>
    ) },
    { key: "contact", header: "Contact", render: r => (
      <span className="gst-small gst-mono">{[r.phone, r.email].filter(v => typeof v === "string" && v).join(" · ") || <span className="gst-faint">-</span>}</span>
    ) },
  ];

  return (
    <Card
      title="In-house roster"
      subtitle="Who is in each room, as the concierge sees it. Editing a room here updates what the concierge knows."
      actions={can(PERM.manage) ? <Button size="sm" onClick={() => setOpen({ room: null })}>Add room</Button> : undefined}
    >
      <div className="gst-toolbar" style={{ marginBottom: 12 }}>
        <div className="gst-grow">
          <Input aria-label="Filter by room or name" placeholder="Filter by room or name" value={filter} onChange={e => setFilter(e.target.value)} />
        </div>
        <span className="gst-small gst-muted">{fmt.number(rows.length)} rooms</span>
      </div>
      <Table
        columns={cols}
        rows={rows}
        rowKey={r => r.roomNumber}
        onRowClick={can(PERM.manage) ? (r => setOpen({ room: r.roomNumber })) : undefined}
        loading={res.loading && !res.data}
        empty={res.error ? res.error.message : "No rooms on the roster. PMS-connected hotels fill this from reservations."}
        caption="In-house roster"
      />
      {open && (
        <RoomEditor
          room={open.room ? (res.data?.[open.room] ?? { roomNumber: open.room }) : null}
          onClose={() => setOpen(null)}
          onSaved={() => res.reload()}
        />
      )}
    </Card>
  );
}
