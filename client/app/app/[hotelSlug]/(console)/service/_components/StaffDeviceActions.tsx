"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Staff card actions that replace "copy personal link" (03 spec 1.8):
   Pair device, Set PIN, Roster. Personal links are being retired: shared
   floor devices with a per-person PIN keep the audit trail honest (who
   acked, on which device) and survive a phone left in a locker.

   PIN: PUT /hotels/{slug}/staff/{id}/pin {pin|null} (foundation contract).
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useState } from "react";
import { LuKeyRound, LuTablet, LuCalendarDays } from "react-icons/lu";
import { api, hotelPath } from "../../../../../lib/api";
import { useSession } from "../../../../../lib/session";
import { Button, Field, Input, Modal, useToast } from "../../../../../ui";
import { errorText } from "./serviceApi";

const iconBtn = {
  background: "none", border: "none", color: "rgba(225, 175, 104,0.7)", cursor: "pointer", padding: "4px",
  display: "inline-flex", alignItems: "center",
} as const;

export function StaffDeviceActions({ hotelSlug, member }: { hotelSlug: string; member: { id: number; name: string } }) {
  const { can } = useSession();
  const toast = useToast();
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState("");
  const [saving, setSaving] = useState(false);
  const canDevices = can(["core.devices.manage", "service.manage"]);
  const base = `/app/${encodeURIComponent(hotelSlug)}`;
  const pinValid = /^\d{4,6}$/.test(pin);

  const savePin = async (value: string | null) => {
    setSaving(true);
    try {
      await api.put(hotelPath(hotelSlug, `/staff/${member.id}/pin`), { pin: value });
      toast.success(value ? `PIN set for ${member.name}.` : `PIN cleared for ${member.name}.`);
      setPinOpen(false);
      setPin("");
    } catch (err) {
      toast.error("Could not save the PIN", errorText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      {canDevices && (
        <Link href={`${base}/settings/devices`} title="Pair a floor device" aria-label="Pair a floor device" style={iconBtn}>
          <LuTablet size={14} />
        </Link>
      )}
      {canDevices && (
        <button type="button" title={`Set PIN for ${member.name}`} aria-label={`Set PIN for ${member.name}`}
                style={iconBtn} onClick={() => setPinOpen(true)}>
          <LuKeyRound size={14} />
        </button>
      )}
      <Link href={`${base}/service/roster`} title="Roster" aria-label="Roster" style={iconBtn}>
        <LuCalendarDays size={14} />
      </Link>

      <Modal open={pinOpen} onClose={() => { setPinOpen(false); setPin(""); }} title={`PIN for ${member.name}`} size="sm"
             dismissible={!saving}
             footer={
               <>
                 <Button variant="ghost" onClick={() => void savePin(null)} disabled={saving}>Clear PIN</Button>
                 <Button variant="primary" onClick={() => void savePin(pin)} disabled={!pinValid} loading={saving}>Save PIN</Button>
               </>
             }>
        <Field label="New PIN" hint="4 to 6 digits. They type it on a paired floor device to start a shift." htmlFor="svc-pin">
          <Input id="svc-pin" inputMode="numeric" autoComplete="off" maxLength={6} value={pin}
                 onChange={e => setPin(e.target.value.replace(/\D/g, ""))}
                 onKeyDown={e => { if (e.key === "Enter" && pinValid) void savePin(pin); }} />
        </Field>
      </Modal>
    </>
  );
}
