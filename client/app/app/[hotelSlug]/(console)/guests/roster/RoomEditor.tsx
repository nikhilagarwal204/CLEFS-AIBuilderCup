"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The GuestModal, moved from StaffDashboard. PUT replaces the room's whole
   blob, so the form starts from the stored object and spreads it back:
   keys this form does not edit (the masked contact copies left by the
   hourly scrub, anything the PMS sync adds) survive a save.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Drawer, Field, Input, Select, useToast } from "../../../../../ui";
import { errText, gstApi, splitList } from "../lib";
import type { RosterGuest, RosterRoom } from "../types";
import IdentityForm from "./IdentityForm";

const STAY_TYPES = ["solo", "couple", "family", "business", "group"];
const GUEST_TYPES = ["adult", "child", "infant"];
const TIERS = ["", "member", "discoverist", "explorist", "globalist"];

export default function RoomEditor({
  room, onClose, onSaved,
}: {
  room: RosterRoom | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const isNew = room === null;
  const [roomNumber, setRoomNumber] = useState(room?.roomNumber ?? "");
  const [guests, setGuests] = useState<RosterGuest[]>(room?.guests?.length ? room.guests : [{ name: "", type: "adult" }]);
  const [stayType, setStayType] = useState(room?.stayType ?? "");
  const [checkIn, setCheckIn] = useState(room?.checkIn ?? "");
  const [checkOut, setCheckOut] = useState(room?.checkOut ?? "");
  const [tags, setTags] = useState((room?.preferences?.tags ?? []).join(", "));
  const [language, setLanguage] = useState(room?.preferences?.language ?? "");
  const [tier, setTier] = useState(room?.loyaltyTier ?? "");
  const [occasions, setOccasions] = useState((room?.occasions ?? []).join(", "));
  const [busy, setBusy] = useState<"" | "save" | "delete">("");

  const setGuest = (i: number, patch: Partial<RosterGuest>) =>
    setGuests(gs => gs.map((g, j) => (j === i ? { ...g, ...patch } : g)));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const num = roomNumber.trim();
    if (!num) return;
    setBusy("save");
    const data: RosterRoom = {
      ...(room ?? {}),
      roomNumber: num,
      guests: guests.filter(g => g.name.trim()).map(g => ({ ...g, name: g.name.trim() })),
      stayType: stayType || undefined,
      checkIn: checkIn || undefined,
      checkOut: checkOut || undefined,
      preferences: { ...(room?.preferences ?? {}), tags: splitList(tags), language: language || undefined },
      loyaltyTier: tier || undefined,
      occasions: splitList(occasions),
    };
    try {
      await api.put(gstApi(slug, `/${encodeURIComponent(num)}`), { data });
      toast.success(`Room ${num} saved`);
      onSaved();
      if (isNew) onClose();
    } catch (err) {
      toast.error("Could not save", errText(err));
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    if (!room || !window.confirm(`Clear room ${room.roomNumber} from the roster?`)) return;
    setBusy("delete");
    try {
      await api.del(gstApi(slug, `/${encodeURIComponent(room.roomNumber)}`));
      toast.success(`Room ${room.roomNumber} cleared`);
      onSaved();
      onClose();
    } catch (err) {
      toast.error("Could not clear the room", errText(err));
    } finally {
      setBusy("");
    }
  }

  return (
    <Drawer open onClose={onClose} title={isNew ? "Add room" : `Room ${room.roomNumber}`} wide>
      <form className="gst-form-grid" onSubmit={save}>
        {isNew && (
          <Field label="Room number" htmlFor="gst-re-room" required>
            <Input id="gst-re-room" required maxLength={16} value={roomNumber} onChange={e => setRoomNumber(e.target.value)} />
          </Field>
        )}
        <Field label="Stay type" htmlFor="gst-re-type">
          <Select id="gst-re-type" value={stayType} onChange={e => setStayType(e.target.value)} placeholder="Not set"
            options={STAY_TYPES.map(s => ({ value: s, label: s[0].toUpperCase() + s.slice(1) }))} />
        </Field>
        <Field label="Check-in" htmlFor="gst-re-in">
          <Input id="gst-re-in" type="date" value={checkIn} onChange={e => setCheckIn(e.target.value)} />
        </Field>
        <Field label="Check-out" htmlFor="gst-re-out">
          <Input id="gst-re-out" type="date" value={checkOut} min={checkIn || undefined} onChange={e => setCheckOut(e.target.value)} />
        </Field>

        <div className="gst-form-grid__wide">
          <div className="gst-section-title" style={{ marginTop: 0 }}>Guests</div>
          <div className="cx-stack" style={{ gap: 8 }}>
            {guests.map((g, i) => (
              <div key={i} className="gst-guest-line">
                <Input aria-label={`Guest ${i + 1} name`} placeholder="Name" value={g.name} maxLength={80}
                  onChange={e => setGuest(i, { name: e.target.value })} />
                <Select aria-label={`Guest ${i + 1} type`} value={g.type || "adult"} onChange={e => setGuest(i, { type: e.target.value })}
                  options={GUEST_TYPES.map(t => ({ value: t, label: t }))} />
                <Input aria-label={`Guest ${i + 1} age`} type="number" min={0} max={120} placeholder="Age" value={g.age ?? ""}
                  onChange={e => setGuest(i, { age: e.target.value === "" ? undefined : Number(e.target.value) })} />
                <Button type="button" size="sm" variant="ghost" aria-label={`Remove guest ${i + 1}`}
                  onClick={() => setGuests(gs => (gs.length > 1 ? gs.filter((_, j) => j !== i) : [{ name: "", type: "adult" }]))}>x</Button>
              </div>
            ))}
            <div><Button type="button" size="sm" variant="secondary" onClick={() => setGuests(gs => [...gs, { name: "", type: "adult" }])}>Add guest</Button></div>
          </div>
        </div>

        <Field label="Preferences" htmlFor="gst-re-tags" hint="Comma separated" className="gst-form-grid__wide">
          <Input id="gst-re-tags" value={tags} onChange={e => setTags(e.target.value)} />
        </Field>
        <Field label="Occasions" htmlFor="gst-re-occ" hint="Comma separated, e.g. anniversary">
          <Input id="gst-re-occ" value={occasions} onChange={e => setOccasions(e.target.value)} />
        </Field>
        <Field label="Language" htmlFor="gst-re-lang">
          <Input id="gst-re-lang" value={language} maxLength={10} placeholder="en, ar, ru..." onChange={e => setLanguage(e.target.value)} />
        </Field>
        <Field label="Loyalty tier" htmlFor="gst-re-tier">
          <Select id="gst-re-tier" value={tier} onChange={e => setTier(e.target.value)}
            options={TIERS.map(t => ({ value: t, label: t ? t[0].toUpperCase() + t.slice(1) : "None" }))} />
        </Field>
        <div className="cx-row gst-form-grid__wide" style={{ justifyContent: "space-between" }}>
          {!isNew ? <Button type="button" variant="danger" size="sm" loading={busy === "delete"} onClick={remove}>Clear room</Button> : <span />}
          <Button type="submit" loading={busy === "save"}>Save room</Button>
        </div>
      </form>

      {!isNew && (
        <IdentityForm
          room={room.roomNumber}
          defaults={{ firstName: room.guests?.[0]?.name?.split(/\s+/)[0] ?? "", checkIn: room.checkIn, checkOut: room.checkOut }}
          onSaved={onSaved}
        />
      )}
    </Drawer>
  );
}
