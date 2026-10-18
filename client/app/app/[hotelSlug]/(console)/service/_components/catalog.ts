"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Service catalog on the client: departments and request types.

   The server is the source (config routes merge the hotel's overrides over
   the global seeds in service/constants.py). Until a hotel's config route
   answers, the seeds below keep the phone console usable; they mirror
   constants.py and are display-only (the server re-resolves every key).
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { api, hotelPath } from "../../../../../lib/api";

// ─── Types ───

export interface Department {
  key: string;
  name: string;
  name_ar?: string | null;
  legacy_category: string;
  color?: string | null;
  active?: boolean;
  sort?: number;
}

export interface RequestType {
  key: string;
  label_en: string;
  label_ar?: string | null;
  department_key: string;
  default_severity: "L1" | "L2" | "L3";
  is_amenity: boolean;
  sensitive?: boolean;
  legacy_type?: string | null;
  active?: boolean;
}

export interface AmenityItem {
  id: number;
  key?: string;
  name: string;
  request_type?: string | null;
  max_qty?: number | null;
}

// ─── Seeds (mirror of service/constants.py) ───

export const SEED_DEPARTMENTS: Department[] = [
  { key: "housekeeping", name: "Housekeeping", legacy_category: "Housekeeping", sort: 10 },
  { key: "ird", name: "In-Room Dining", legacy_category: "Food & Beverage", sort: 20 },
  { key: "engineering", name: "Engineering", legacy_category: "Maintenance", sort: 30 },
  { key: "front_office", name: "Front Office", legacy_category: "Front Desk", sort: 40 },
  { key: "concierge", name: "Concierge", legacy_category: "Concierge", sort: 50 },
  { key: "security", name: "Security", legacy_category: "Security", sort: 60 },
  { key: "general", name: "General", legacy_category: "General", sort: 70 },
];

// legacy_type: the old `type` string (constants.REQUEST_TYPES), used only on
// the legacy write path so old screens keep their icons and filters.
const LEGACY_TYPE: Record<string, string> = {
  towels: "Towel", linen: "Clean Sheets", water: "Water", ird: "In-Room Dining", breakfast: "In-Room Dining",
  drinks: "Food", luggage: "Luggage Assistance", spa: "Spa Booking", plumbing: "Water Supply", wake_up: "Wake-Up Call",
  billing: "Billing", checkout: "Checkout", front_desk: "Front Desk", direct_conversation: "Direct Conversation",
  complaint: "Direct Conversation", emergency: "Direct Conversation", ac: "Maintenance", electrical: "Maintenance",
  maintenance: "Maintenance", amenities: "Cleaning", pillows: "Cleaning", extra_bed: "Cleaning",
  make_up_room: "Cleaning", turndown: "Cleaning",
};

const rt = (key: string, label_en: string, department_key: string, sev: "L1" | "L2" | "L3" = "L3", is_amenity = false): RequestType =>
  ({ key, label_en, department_key, default_severity: sev, is_amenity, legacy_type: LEGACY_TYPE[key] ?? "Other" });

export const SEED_REQUEST_TYPES: RequestType[] = [
  rt("towels", "Towels", "housekeeping", "L3", true),
  rt("linen", "Linen", "housekeeping", "L3", true),
  rt("amenities", "Amenities", "housekeeping", "L3", true),
  rt("water", "Drinking water", "housekeeping", "L3", true),
  rt("pillows", "Pillows", "housekeeping", "L3", true),
  rt("extra_bed", "Extra bed", "housekeeping", "L3", true),
  rt("make_up_room", "Make up room", "housekeeping"),
  rt("turndown", "Turndown", "housekeeping"),
  rt("ird", "In-room dining", "ird"),
  rt("breakfast", "Breakfast", "ird"),
  rt("drinks", "Drinks", "ird"),
  rt("ac", "Air conditioning", "engineering", "L2"),
  rt("plumbing", "Plumbing / water supply", "engineering", "L2"),
  rt("electrical", "Electrical", "engineering", "L2"),
  rt("maintenance", "Maintenance", "engineering", "L2"),
  rt("wake_up", "Wake-up call", "front_office"),
  rt("billing", "Billing", "front_office"),
  rt("checkout", "Checkout", "front_office"),
  rt("front_desk", "Front desk", "front_office"),
  rt("direct_conversation", "Talk to a person", "front_office", "L2"),
  rt("complaint", "Complaint", "front_office", "L2"),
  rt("luggage", "Luggage", "concierge"),
  rt("concierge", "Concierge", "concierge"),
  rt("spa", "Spa booking", "concierge"),
  rt("security", "Security", "security", "L2"),
  rt("emergency", "Emergency", "security", "L1"),
  rt("other", "Other", "general"),
];

/** Accept either a bare list or {items: [...]} from a config route. */
export function listOf<T>(body: unknown): T[] {
  if (Array.isArray(body)) return body as T[];
  const items = (body as { items?: unknown } | null)?.items;
  return Array.isArray(items) ? (items as T[]) : [];
}

// ─── Hook ───

export interface Catalog {
  departments: Department[];
  requestTypes: RequestType[];
  amenities: AmenityItem[];
  /** True when the lists came from the server (not the seeds). */
  live: boolean;
}

export function useServiceCatalog(slug: string): Catalog {
  const [cat, setCat] = useState<Catalog>({
    departments: SEED_DEPARTMENTS, requestTypes: SEED_REQUEST_TYPES, amenities: [], live: false,
  });
  useEffect(() => {
    let live = true;
    const get = (p: string) => api.get<unknown>(hotelPath(slug, p)).catch(() => null);
    Promise.all([get("/service/config/departments"), get("/service/config/request-types"), get("/amenities/items")])
      .then(([d, t, a]) => {
        if (!live) return;
        const departments = listOf<Department>(d).filter(x => x.active !== false);
        const requestTypes = listOf<RequestType>(t).filter(x => x.active !== false);
        setCat({
          departments: departments.length ? departments.sort((x, y) => (x.sort ?? 0) - (y.sort ?? 0)) : SEED_DEPARTMENTS,
          requestTypes: requestTypes.length ? requestTypes : SEED_REQUEST_TYPES,
          amenities: listOf<AmenityItem>(a),
          live: departments.length > 0 || requestTypes.length > 0,
        });
      });
    return () => { live = false; };
  }, [slug]);
  return cat;
}
