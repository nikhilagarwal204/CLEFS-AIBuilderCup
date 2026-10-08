"use client";

/* RoomEditor: add one room, add a numbered range ("101 to 120 on floor
   1"), or edit an existing room. Floors are free text ("12", "G",
   "Penthouse") because they drive device scoping and routing, not maths;
   leaving the floor empty asks the server to infer it from the number. */

import { useState } from "react";
import { Button, Field, Input, Modal, Segmented, Select } from "../../../../../ui";
import { expandRange, inferFloor, SERVICE_LABEL, validRoom, type Room, type RoomInput, type ServiceState } from "./rooms";

type Mode = "one" | "range";

export interface RoomEditorProps {
  open: boolean;
  /** null: adding. */
  room: Room | null;
  existing: ReadonlySet<string>;
  saving: boolean;
  onClose: () => void;
  onSave: (rooms: RoomInput[]) => void;
}

const SERVICE_OPTIONS = (Object.keys(SERVICE_LABEL) as ServiceState[]).map(k => ({ value: k, label: SERVICE_LABEL[k] }));

export function RoomEditor({ open, room, existing, saving, onClose, onSave }: RoomEditorProps) {
  const [mode, setMode] = useState<Mode>("one");
  const [number, setNumber] = useState(room?.room_number ?? "");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [floor, setFloor] = useState(room && !room.floor_inferred ? room.floor ?? "" : "");
  const [wing, setWing] = useState(room?.wing ?? "");
  const [beds, setBeds] = useState(room?.bedrooms ? String(room.bedrooms) : "");
  const [ext, setExt] = useState(room?.extension ?? "");
  const [state, setState] = useState<ServiceState>(room?.service_state ?? "in_service");
  const [reason, setReason] = useState(room?.ooo_reason ?? "");

  const editing = room !== null;
  const numbers = mode === "range" && !editing ? expandRange(from.trim(), to.trim()) : [number.trim()];
  const clash = !editing && numbers.filter(n => existing.has(n));
  const bad = numbers.filter(n => n && !validRoom(n));
  const bedsOk = !beds || (Number.isInteger(Number(beds)) && Number(beds) >= 1 && Number(beds) <= 20);
  const ready = numbers.length > 0 && numbers.every(Boolean) && !bad.length && bedsOk;
  const hintFloor = numbers[0] ? inferFloor(numbers[0]) : null;

  const save = () => {
    // PUT /rooms drops None fields (model_dump(exclude_none)), so null means
    // "keep what is stored". Clearing a text field on an edit therefore sends
    // "", and "" for floor asks the server to infer it from the number again.
    const blank = editing ? "" : null;
    const common: Omit<RoomInput, "room_number"> = {
      floor: floor.trim() || blank,
      wing: wing.trim() || blank,
      bedrooms: beds ? Number(beds) : null,
      service_state: state,
      ooo_reason: state === "in_service" ? blank : reason.trim() || blank,
    };
    onSave(numbers.map(n => ({ room_number: n, ...common, ...(mode === "one" ? { extension: ext.trim() || blank } : {}) })));
  };

  return (
    <Modal open={open} onClose={onClose} title={editing ? `Room ${room.room_number}` : "Add rooms"}
           footer={
             <div className="cx-row" style={{ justifyContent: "flex-end" }}>
               <Button variant="ghost" onClick={onClose}>Cancel</Button>
               <Button loading={saving} disabled={!ready} onClick={save}>
                 {editing ? "Save room" : numbers.length > 1 ? `Add ${numbers.length} rooms` : "Add room"}
               </Button>
             </div>
           }>
      <div className="cx-stack" style={{ gap: 12 }}>
        {!editing && (
          <Segmented ariaLabel="Add one or a range" value={mode} onChange={setMode}
                     options={[{ value: "one", label: "One room" }, { value: "range", label: "A range" }]} />
        )}
        {mode === "one" || editing ? (
          <Field label="Room number" required error={bad.length ? "Letters, digits, space, dot, dash; up to 24" : undefined}>
            <Input value={number} disabled={editing} maxLength={24} onChange={e => setNumber(e.target.value)} autoFocus={!editing} />
          </Field>
        ) : (
          <div className="cx-grid cx-grid--2">
            <Field label="From"><Input inputMode="numeric" value={from} onChange={e => setFrom(e.target.value.replace(/[^\d]/g, ""))} /></Field>
            <Field label="To" hint={numbers.length ? `${numbers.length} rooms` : "Up to 500 at a time"}>
              <Input inputMode="numeric" value={to} onChange={e => setTo(e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
          </div>
        )}
        {clash && clash.length > 0 && (
          <p className="rms-note">Already registered, will be updated: {clash.slice(0, 8).join(", ")}{clash.length > 8 ? "..." : ""}</p>
        )}
        <div className="cx-grid cx-grid--2">
          <Field label="Floor" hint={hintFloor ? `Empty means floor ${hintFloor}, read from the number` : "Empty means read from the number"}>
            <Input value={floor} maxLength={24} onChange={e => setFloor(e.target.value)} />
          </Field>
          <Field label="Wing or tower"><Input value={wing} maxLength={60} onChange={e => setWing(e.target.value)} /></Field>
          {room?.room_type_code && (
            <Field label="Room type" hint="Set from your PMS room types">
              <Input value={room.room_type_code} disabled />
            </Field>
          )}
          <Field label="Bedrooms" hint="Tourism Dirham is charged per bedroom" error={bedsOk ? undefined : "1 to 20"}>
            <Input inputMode="numeric" value={beds} onChange={e => setBeds(e.target.value.replace(/[^\d]/g, ""))} />
          </Field>
          {(mode === "one" || editing) && (
            <Field label="Phone extension"><Input value={ext} maxLength={20} onChange={e => setExt(e.target.value)} /></Field>
          )}
          <Field label="Service state">
            <Select value={state} options={SERVICE_OPTIONS} onChange={e => setState(e.target.value as ServiceState)} />
          </Field>
        </div>
        {state !== "in_service" && (
          <Field label="Reason"><Input value={reason} maxLength={200} onChange={e => setReason(e.target.value)} /></Field>
        )}
      </div>
    </Modal>
  );
}
