"use client";

/* Outlets: where food comes from (in-room dining, the pool bar ...). Each has
   hotel-local opening windows and a "delivers to rooms" switch the guest
   menu and the quote gate both honour. The key is fixed after creation
   because CSV imports and the concierge refer to outlets by key. */

import { useState } from "react";
import { api, hotelPath } from "../../../../lib/api";
import {
  Badge, Button, Card, Drawer, EmptyState, Field, Input, Select, Table, useToast, type Column,
} from "../../../../ui";
import HoursEditor, { hoursValid } from "./HoursEditor";
import { OUTLET_KINDS, errText, hoursText, outletKindLabel } from "./lib";
import type { HoursWindow, MenuData, Outlet, OutletKind } from "./types";

// ─── Form state ──────────────────────────────────────────────────────────

interface OutletForm {
  key: string;
  name_en: string;
  name_ar: string;
  kind: OutletKind;
  hours: HoursWindow[];
  delivers_to_room: boolean;
  active: boolean;
  sort: string;
}

const EMPTY: OutletForm = {
  key: "", name_en: "", name_ar: "", kind: "in_room_dining", hours: [], delivers_to_room: true, active: true, sort: "0",
};

function toForm(o: Outlet): OutletForm {
  return {
    key: o.key, name_en: o.name_en, name_ar: o.name_ar ?? "", kind: o.kind, hours: o.hours ?? [],
    delivers_to_room: o.delivers_to_room, active: o.active, sort: String(o.sort ?? 0),
  };
}

const KEY_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;

// ─── Tab ─────────────────────────────────────────────────────────────────

export default function OutletsTab({ data }: { data: MenuData }) {
  const toast = useToast();
  const [editing, setEditing] = useState<Outlet | "new" | null>(null);
  const countFor = (id: number) => data.items.filter(i => i.outlet_id === id).length;

  const columns: Column<Outlet>[] = [
    {
      key: "name", header: "Outlet",
      render: o => (
        <div>
          <div className="ups-item-name">{o.name_en}</div>
          {o.name_ar && <div className="ups-item-ar" lang="ar">{o.name_ar}</div>}
          <div className="ups-sub">{o.key}</div>
        </div>
      ),
    },
    { key: "kind", header: "Type", render: o => outletKindLabel(o.kind) },
    { key: "hours", header: "Hours", render: o => <span className="ups-sub">{hoursText(o.hours)}</span> },
    { key: "items", header: "Items", align: "right", render: o => countFor(o.id) },
    {
      key: "state", header: "Status",
      render: o => (
        <div className="ups-tags">
          <Badge tone={o.active ? "ok" : "neutral"}>{o.active ? "Active" : "Hidden"}</Badge>
          {o.delivers_to_room && <Badge tone="info">Room delivery</Badge>}
        </div>
      ),
    },
  ];

  return (
    <Card
      title="Outlets"
      subtitle="Each outlet has its own hours. Guests only see items from active outlets that are open."
      actions={data.canManage && <Button variant="primary" size="sm" onClick={() => setEditing("new")}>Add outlet</Button>}
    >
      <Table
        columns={columns}
        rows={data.outlets}
        rowKey={o => o.id}
        loading={data.loading}
        onRowClick={data.canManage ? o => setEditing(o) : undefined}
        empty={
          <EmptyState
            title="No outlets yet"
            body="Start with in-room dining, then add the pool bar or cafe if guests order from them."
            action={data.canManage && <Button variant="primary" onClick={() => setEditing("new")}>Add outlet</Button>}
          />
        }
      />
      {editing && (
        <OutletEditor
          data={data}
          outlet={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={msg => { toast.success(msg); setEditing(null); data.reloadOutlets(); data.reloadItems(); }}
        />
      )}
    </Card>
  );
}

// ─── Editor ──────────────────────────────────────────────────────────────

function OutletEditor({ data, outlet, onClose, onSaved }: {
  data: MenuData;
  outlet: Outlet | null;
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const toast = useToast();
  const [f, setF] = useState<OutletForm>(outlet ? toForm(outlet) : EMPTY);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const set = (patch: Partial<OutletForm>) => setF(prev => ({ ...prev, ...patch }));

  const keyOk = outlet !== null || KEY_RE.test(f.key.trim().toLowerCase());
  const valid = keyOk && f.name_en.trim() !== "" && hoursValid(f.hours);

  const save = async () => {
    if (!valid) return;
    setBusy(true);
    const body = {
      name_en: f.name_en.trim(),
      name_ar: f.name_ar.trim() || null,
      kind: f.kind,
      hours: f.hours,
      delivers_to_room: f.delivers_to_room,
      active: f.active,
      sort: Number(f.sort) || 0,
    };
    try {
      if (outlet) await api.patch(hotelPath(data.slug, `/menu/outlets/${outlet.id}`), body);
      else await api.post(hotelPath(data.slug, "/menu/outlets"), { ...body, key: f.key.trim().toLowerCase() });
      onSaved(outlet ? "Outlet saved" : "Outlet added");
    } catch (err) {
      toast.error("Could not save the outlet", errText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!outlet) return;
    setBusy(true);
    try {
      await api.del(hotelPath(data.slug, `/menu/outlets/${outlet.id}`));
      onSaved("Outlet deleted");
    } catch (err) {
      toast.error("Could not delete the outlet", errText(err));
      setBusy(false);
    }
  };

  const itemCount = outlet ? data.items.filter(i => i.outlet_id === outlet.id).length : 0;

  return (
    <Drawer
      open
      onClose={onClose}
      title={outlet ? `Edit ${outlet.name_en}` : "Add outlet"}
      footer={
        <div className="ups-actions" style={{ width: "100%" }}>
          {outlet && !confirmDelete && (
            <Button variant="ghost" onClick={() => setConfirmDelete(true)} disabled={busy}>Delete</Button>
          )}
          {outlet && confirmDelete && (
            <Button variant="danger" onClick={remove} loading={busy}>
              {itemCount ? `Delete with ${itemCount} items` : "Confirm delete"}
            </Button>
          )}
          <span className="ups-spacer" />
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={busy} disabled={!valid}>Save</Button>
        </div>
      }
    >
      <div className="ups-form ups-form--2">
        <Field label="Name (English)" required>
          <Input value={f.name_en} onChange={e => set({ name_en: e.target.value })} maxLength={120} />
        </Field>
        <Field label="Name (Arabic)">
          <Input className="ups-rtl" dir="rtl" lang="ar" value={f.name_ar} maxLength={120}
                 onChange={e => set({ name_ar: e.target.value })} />
        </Field>
        <Field label="Key" required hint={outlet ? "Fixed once created." : "Lowercase letters, digits, - or _. Used in CSV imports."}
               error={!keyOk && f.key ? "Use lowercase letters, digits, - or _" : undefined}>
          <Input value={f.key} disabled={outlet !== null} placeholder="ird"
                 onChange={e => set({ key: e.target.value })} maxLength={40} />
        </Field>
        <Field label="Type">
          <Select value={f.kind} options={OUTLET_KINDS} onChange={e => set({ kind: e.target.value as OutletKind })} />
        </Field>
        <Field label="Sort order" hint="Lower shows first.">
          <Input type="number" inputMode="numeric" value={f.sort} onChange={e => set({ sort: e.target.value })} />
        </Field>
        <div className="cx-stack" style={{ gap: 8, justifyContent: "center" }}>
          <label className="ups-check">
            <input type="checkbox" checked={f.delivers_to_room} onChange={e => set({ delivers_to_room: e.target.checked })} />
            Delivers to rooms
          </label>
          <label className="ups-check">
            <input type="checkbox" checked={f.active} onChange={e => set({ active: e.target.checked })} />
            Active (shown to guests)
          </label>
        </div>
        {/* Not a <Field>: that wraps its children in a <label>, and a label
            must not hold several buttons. */}
        <div className="ups-span cx-field" role="group" aria-labelledby="ups-outlet-hours">
          <span className="cx-label" id="ups-outlet-hours">Opening hours <span className="cx-faint">(hotel local time)</span></span>
          <HoursEditor value={f.hours} onChange={hours => set({ hours })} />
        </div>
      </div>
    </Drawer>
  );
}
