/* ═══════════════════════════════════════════════════════════════════════
   Service config calls (03 spec 1.7: GET/PUT config/{departments,
   request-types, sla-policies, compensation}) and the room register import.

   Shapes follow the DDL in migrations 0101 (svc_departments,
   svc_request_types, svc_sla_policies, svc_compensation_authority). The
   server (service/config_api.py) answers a bare list, except compensation,
   which answers the matrix {currency, kinds, roles}. A PUT sends the whole
   section under the section's own key ({departments}, {request_types},
   {policies}, {roles} with max_value in major units), hotel overrides
   merged over the seeds on the server. {items} is still read for safety.

   Rooms go through the foundation register (PUT /hotels/{slug}/rooms), so a
   CSV import works whether or not the service import route exists.
   ═══════════════════════════════════════════════════════════════════════ */

import { api, hotelPath } from "../../../../../lib/api";
import { listOf, type Department, type RequestType } from "./catalog";

export type ConfigSection = "departments" | "request-types" | "sla-policies" | "compensation";

export interface SlaPolicy {
  id?: number;
  scope_type: "request_type" | "department_severity" | "department" | "severity" | "default";
  scope_key: string;
  ack_min: number;
  resolve_min: number;
  due_local?: string | null;
  ladder?: { after_min: number; to: string }[] | null;
  active?: boolean;
}

export interface CompensationRule {
  role_key: "staff" | "supervisor" | "duty_manager" | "gm";
  max_minor: number | null;
  currency: string;
  allowed_kinds: string[];
}

export type SectionRow = Department | RequestType | SlaPolicy | CompensationRule;

/** A section's rows from any answer shape (list, {items}, or the
    compensation matrix's {roles}). */
function rowsOf<T>(body: unknown): T[] {
  const roles = (body as { roles?: unknown } | null)?.roles;
  return Array.isArray(roles) ? (roles as T[]) : listOf<T>(body);
}

/** Digits after the decimal point of a currency (AED 2, JPY 0, KWD 3). */
function currencyExponent(currency: string): number {
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** Minor units to an exact major-unit string (no float rounding). */
function minorToMajor(minor: number, currency: string): string {
  const exp = currencyExponent(currency);
  if (exp === 0) return String(minor);
  const neg = minor < 0;
  const digits = String(Math.abs(Math.trunc(minor))).padStart(exp + 1, "0");
  return `${neg ? "-" : ""}${digits.slice(0, -exp)}.${digits.slice(-exp)}`;
}

/** The PUT body config_api.py expects for a section. */
function bodyFor<T>(section: ConfigSection, items: T[]): unknown {
  switch (section) {
    case "departments": return { departments: items };
    case "request-types": return { request_types: items };
    case "sla-policies": return { policies: items };
    case "compensation":
      return {
        roles: (items as unknown as CompensationRule[]).map(r => ({
          role_key: r.role_key,
          max_value: r.max_minor == null ? null : minorToMajor(r.max_minor, r.currency || "AED"),
          allowed_kinds: r.allowed_kinds,
        })),
      };
  }
}

export async function loadSection<T>(slug: string, section: ConfigSection): Promise<T[]> {
  return rowsOf<T>(await api.get<unknown>(hotelPath(slug, `/service/config/${section}`)));
}

export async function saveSection<T>(slug: string, section: ConfigSection, items: T[]): Promise<T[]> {
  const out = await api.put<unknown>(hotelPath(slug, `/service/config/${section}`), bodyFor(section, items));
  const list = rowsOf<T>(out);
  return list.length ? list : items;
}

// ─── Room register import ───

export interface RoomImportRow {
  room_number: string;
  floor?: string;
  wing?: string;
  room_type_code?: string;
  extension?: string;
}

const HEADER_ALIASES: Record<string, keyof RoomImportRow> = {
  room: "room_number", room_number: "room_number", "room no": "room_number", number: "room_number",
  floor: "floor", level: "floor",
  wing: "wing", tower: "wing", block: "wing",
  type: "room_type_code", room_type: "room_type_code", "room type": "room_type_code", category: "room_type_code",
  extension: "extension", ext: "extension", phone: "extension",
};

/** Split one CSV line, honouring double quotes ("a, b" stays one cell). */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === "," || ch === ";" || ch === "\t") { out.push(cur.trim()); cur = ""; }
    else cur += ch;
  }
  out.push(cur.trim());
  return out;
}

/** CSV with a header row (room, floor, wing, type, extension) to register rows.
    Room numbers follow core.rooms.ROOM_RE. */
export function parseRoomsCsv(text: string): { rows: RoomImportRow[]; errors: string[] } {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return { rows: [], errors: ["The file is empty."] };
  const header = splitCsvLine(lines[0]).map(h => HEADER_ALIASES[h.toLowerCase()] ?? null);
  if (!header.includes("room_number")) return { rows: [], errors: ["The first row needs a 'room' column."] };
  const rows: RoomImportRow[] = [];
  const errors: string[] = [];
  const seen = new Set<string>();
  lines.slice(1).forEach((line, i) => {
    const cells = splitCsvLine(line);
    const row: RoomImportRow = { room_number: "" };
    header.forEach((key, c) => { if (key && cells[c]) row[key] = cells[c]; });
    if (!row.room_number) { errors.push(`Row ${i + 2}: no room number.`); return; }
    if (!/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,23}$/.test(row.room_number)) { errors.push(`Row ${i + 2}: "${row.room_number}" is not a room number.`); return; }
    if (seen.has(row.room_number)) { errors.push(`Row ${i + 2}: room ${row.room_number} appears twice.`); return; }
    seen.add(row.room_number);
    rows.push(row);
  });
  return { rows, errors };
}

export function importRooms(slug: string, rows: RoomImportRow[]) {
  return api.put<{ ok: boolean; count: number }>(hotelPath(slug, "/rooms"), { rooms: rows });
}
