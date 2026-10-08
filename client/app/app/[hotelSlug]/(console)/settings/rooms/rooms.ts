/* Room register types and the pure CSV-to-rooms mapping for Settings >
   Rooms. Mirrors server core/rooms.py: room numbers are text ("502",
   "PH-1") matching ROOM_RE, a room without a floor gets the floor inferred
   from its number (502 -> 5, 1204 -> 12), and PUT /rooms is a partial
   upsert by number (omitted fields keep their stored value). */

import { findColumn, parseCsv } from "../../../../../ingest/csv";

export type ServiceState = "in_service" | "ooo" | "oos";

export interface Room {
  room_number: string;
  floor: string | null;
  floor_inferred: boolean;
  wing: string | null;
  room_type_code: string | null;
  bedrooms: number | null;
  extension: string | null;
  service_state: ServiceState | null;
  ooo_reason: string | null;
  qr_key_version: number;
  is_active: boolean;
}

/** One item of PUT /hotels/{slug}/rooms (core routes.RoomBody). */
export interface RoomInput {
  room_number: string;
  floor?: string | null;
  wing?: string | null;
  room_type_code?: string | null;
  bedrooms?: number | null;
  extension?: string | null;
  service_state?: ServiceState | null;
  ooo_reason?: string | null;
  is_active?: boolean | null;
}

export const SERVICE_LABEL: Record<ServiceState, string> = {
  in_service: "In service",
  ooo: "Out of order",
  oos: "Out of service",
};

// ─── Server rules, mirrored ───

export const ROOM_RE = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,23}$/;

export function validRoom(n: string): boolean {
  return ROOM_RE.test(n);
}

/** Digits minus the last two: 502 -> "5", 1204 -> "12", 12 -> null. */
export function inferFloor(room: string): string | null {
  const m = /^(\d+)/.exec(room);
  if (!m || m[1].length < 3) return null;
  return String(Number(m[1].slice(0, -2)));
}

/** Floors sorted the way a building reads: numeric first (B1/G/1/2/10), then names. */
export function floorSort(a: string, b: string): number {
  const na = Number(a);
  const nb = Number(b);
  const ia = Number.isFinite(na) && a.trim() !== "";
  const ib = Number.isFinite(nb) && b.trim() !== "";
  if (ia && ib) return na - nb;
  if (ia) return -1;
  if (ib) return 1;
  return a.localeCompare(b, undefined, { numeric: true });
}

export function roomSort(a: Room, b: Room): number {
  return a.room_number.localeCompare(b.room_number, undefined, { numeric: true });
}

// ─── CSV mapping ───

/* room_type_code is deliberately not imported: hotel_rooms.room_type_code
   references room_types(hotel_slug, code), nothing can create room types
   yet, and an unknown code fails the whole PUT with a foreign-key error
   (FOUNDATION-REQUEST in this lane's report). Bedrooms, which is what the
   Tourism Dirham needs, lives on the room itself. */
const TYPE_ALIASES = ["room type", "type", "room type code", "category", "room category"];

const ALIASES: Record<keyof Omit<RoomInput, "is_active" | "ooo_reason" | "room_type_code">, readonly string[]> = {
  room_number: ["room number", "room no", "room", "room num", "number", "rm", "room #", "unit"],
  floor: ["floor", "level", "floor no", "floor number"],
  wing: ["wing", "block", "tower", "building"],
  bedrooms: ["bedrooms", "beds", "bedroom count", "no of bedrooms"],
  extension: ["extension", "ext", "phone extension", "phone", "phone ext"],
  service_state: ["status", "service state", "state"],
};

export interface CsvRooms {
  rooms: RoomInput[];
  errors: { line: number; message: string }[];
  columns: Partial<Record<keyof typeof ALIASES, string>>;
  duplicates: string[];
  /** A room-type column the file has but the import leaves out. */
  ignoredType: string | null;
}

function serviceState(v: string): ServiceState | undefined {
  const s = v.toLowerCase().replace(/[^a-z]/g, "");
  if (!s) return undefined;
  if (["ooo", "outoforder"].includes(s)) return "ooo";
  if (["oos", "outofservice"].includes(s)) return "oos";
  if (["inservice", "active", "ok", "available", "vacant", "clean", "open"].includes(s)) return "in_service";
  return undefined;
}

/** Parse a room list CSV. Empty cells are left out so an update never blanks a stored value. */
export function roomsFromCsv(text: string): CsvRooms {
  const t = parseCsv(text);
  const idx = Object.fromEntries(
    (Object.keys(ALIASES) as (keyof typeof ALIASES)[]).map(k => [k, findColumn(t.headers, ALIASES[k])]),
  ) as Record<keyof typeof ALIASES, number>;
  const columns: CsvRooms["columns"] = {};
  for (const [k, i] of Object.entries(idx)) if (i >= 0) columns[k as keyof typeof ALIASES] = t.headers[i];
  const typeIdx = findColumn(t.headers, TYPE_ALIASES);
  const out: CsvRooms = { rooms: [], errors: [], columns, duplicates: [], ignoredType: typeIdx >= 0 ? t.headers[typeIdx] : null };
  if (idx.room_number < 0) {
    out.errors.push({ line: 1, message: "No room number column. Name one column \"Room\" or \"Room number\"." });
    return out;
  }
  const seen = new Set<string>();
  t.rows.forEach((row, i) => {
    const line = i + 2;
    const cell = (k: keyof typeof ALIASES) => (idx[k] >= 0 ? (row[idx[k]] ?? "").trim() : "");
    const number = cell("room_number");
    if (!number) return;
    if (!validRoom(number)) { out.errors.push({ line, message: `"${number}" is not a valid room number` }); return; }
    if (seen.has(number)) { out.duplicates.push(number); return; }
    seen.add(number);
    const r: RoomInput = { room_number: number };
    if (cell("floor")) r.floor = cell("floor").slice(0, 24);
    if (cell("wing")) r.wing = cell("wing").slice(0, 60);
    if (cell("extension")) r.extension = cell("extension").slice(0, 20);
    const beds = cell("bedrooms");
    if (beds) {
      const n = Number(beds);
      if (!Number.isInteger(n) || n < 1 || n > 20) out.errors.push({ line, message: `bedrooms "${beds}" must be 1 to 20` });
      else r.bedrooms = n;
    }
    const st = cell("service_state");
    if (st) {
      const s = serviceState(st);
      if (s) r.service_state = s;
      else out.errors.push({ line, message: `status "${st}" is not in service, out of order or out of service` });
    }
    out.rooms.push(r);
  });
  return out;
}

export const TEMPLATE_CSV = "Room number,Floor,Wing,Bedrooms,Extension,Status\n101,1,,1,2101,in service\n102,1,,1,2102,out of order\n1201,12,Tower B,2,31201,in service\n";

/** "101-120" style range to room numbers, keeping zero padding ("0101-0105"). */
export function expandRange(from: string, to: string): string[] {
  const a = Number(from);
  const b = Number(to);
  if (!Number.isInteger(a) || !Number.isInteger(b) || a < 0 || b < a || b - a > 500) return [];
  const width = from.length === to.length && from.startsWith("0") ? from.length : 0;
  const out: string[] = [];
  for (let n = a; n <= b; n++) out.push(width ? String(n).padStart(width, "0") : String(n));
  return out;
}
