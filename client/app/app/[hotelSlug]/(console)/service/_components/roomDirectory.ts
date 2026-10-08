"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Room directory for the phone-entry console: the room register (floor,
   type) joined with who is staying (legacy /guests map), for autocomplete.

   Guest names are masked unless the viewer holds guests.view (Canon trust
   layer: a front-desk temp typing a towel request does not need a surname).
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { api, hotelPath } from "../../../../../lib/api";

export interface RoomInfo {
  room: string;
  floor: string | null;
  roomType: string | null;
  guestName: string | null;
  vip: boolean;
  returning: boolean;
  occupied: boolean;
}

interface RegisterRow { room_number: string; floor?: string | null; room_type_code?: string | null; is_active?: boolean }
interface GuestRoomRow {
  roomNumber?: string;
  guests?: { name?: string; type?: string }[];
  loyaltyTier?: string;
  returningGuest?: boolean;
  preferences?: { tags?: string[] };
}

// Mirrors service constants VIP_TIERS / VIP_TAGS (display only).
const VIP_TIERS = new Set(["globalist", "explorist", "platinum", "titanium", "ambassador", "diamond", "gold", "vip", "black"]);
const RETURNING_TAGS = new Set(["returning", "repeat", "loyal"]);

/** "Sara Al Mansoori" -> "S. A." for viewers without guest access. */
export function maskName(name: string, reveal: boolean): string {
  if (reveal) return name;
  return name.split(/\s+/).filter(Boolean).map(w => `${w[0]?.toUpperCase() ?? ""}.`).join(" ");
}

/** "502" -> "5"; "1203A" -> "12". Same rule as core.rooms.infer_floor. */
export function inferFloor(room: string): string | null {
  const digits = /^\d+/.exec(room)?.[0];
  if (!digits || digits.length < 3) return null;
  return String(Number(digits.slice(0, -2)));
}

export function useRoomDirectory(slug: string): { rooms: RoomInfo[]; loading: boolean } {
  const [state, setState] = useState<{ rooms: RoomInfo[]; loading: boolean }>({ rooms: [], loading: true });
  useEffect(() => {
    let live = true;
    Promise.all([
      api.get<RegisterRow[]>(hotelPath(slug, "/rooms")).catch(() => [] as RegisterRow[]),
      api.get<Record<string, GuestRoomRow>>(hotelPath(slug, "/guests")).catch(() => ({} as Record<string, GuestRoomRow>)),
    ]).then(([register, guests]) => {
      if (!live) return;
      const byRoom = new Map<string, RoomInfo>();
      for (const r of Array.isArray(register) ? register : []) {
        if (r.is_active === false || !r.room_number) continue;
        byRoom.set(r.room_number, {
          room: r.room_number, floor: r.floor ?? inferFloor(r.room_number), roomType: r.room_type_code ?? null,
          guestName: null, vip: false, returning: false, occupied: false,
        });
      }
      for (const [key, g] of Object.entries(guests && typeof guests === "object" ? guests : {})) {
        const room = g.roomNumber || key;
        const base = byRoom.get(room) ?? {
          room, floor: inferFloor(room), roomType: null, guestName: null, vip: false, returning: false, occupied: false,
        };
        const primary = g.guests?.find(x => x.type === "primary") ?? g.guests?.[0];
        const tags = (g.preferences?.tags ?? []).map(t => t.toLowerCase());
        byRoom.set(room, {
          ...base,
          occupied: true,
          guestName: primary?.name ?? null,
          vip: VIP_TIERS.has((g.loyaltyTier ?? "").toLowerCase()) || tags.includes("vip"),
          returning: !!g.returningGuest || tags.some(t => RETURNING_TAGS.has(t)),
        });
      }
      const rooms = [...byRoom.values()].sort((a, b) => a.room.localeCompare(b.room, undefined, { numeric: true }));
      setState({ rooms, loading: false });
    });
    return () => { live = false; };
  }, [slug]);
  return state;
}

/** Autocomplete: prefix matches first, then occupied rooms, max `limit`. */
export function matchRooms(rooms: readonly RoomInfo[], q: string, limit = 8): RoomInfo[] {
  const s = q.trim().toLowerCase();
  if (!s) return [];
  const hits = rooms.filter(r => r.room.toLowerCase().startsWith(s) || (r.guestName ?? "").toLowerCase().includes(s));
  hits.sort((a, b) =>
    Number(!a.room.toLowerCase().startsWith(s)) - Number(!b.room.toLowerCase().startsWith(s)) ||
    Number(b.occupied) - Number(a.occupied) ||
    a.room.localeCompare(b.room, undefined, { numeric: true }));
  return hits.slice(0, limit);
}
