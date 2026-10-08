/* ═══════════════════════════════════════════════════════════════════════
   Menu console: wire shapes of /hotels/{slug}/menu/* (upsell module README,
   "HTTP API: console"). Money arrives twice on purpose: a Decimal string for
   display and *_minor integers for arithmetic, so the client never adds
   floats (Canon 2).
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Shared page data ──────────────────────────────────────────────────

/** What page.tsx loads once and hands to every tab. */
export interface MenuData {
  slug: string;
  outlets: Outlet[];
  items: MenuItem[];
  loading: boolean;
  canManage: boolean;
  canOrders: boolean;
  reloadOutlets(): void;
  reloadItems(): void;
}

// ─── Menu structure ──────────────────────────────────────────────────────

export interface HoursWindow {
  /** "HH:MM" hotel-local. A window whose `to` is before `from` runs past midnight. */
  from: string;
  to: string;
  /** Bit 0 = Monday ... bit 6 = Sunday; 127 = every day. */
  days_mask: number;
}

export type OutletKind =
  | "in_room_dining" | "restaurant" | "bar" | "cafe" | "pool" | "spa" | "minibar" | "other";

export interface Outlet {
  id: number;
  key: string;
  name_en: string;
  name_ar: string | null;
  kind: OutletKind;
  hours: HoursWindow[];
  delivers_to_room: boolean;
  active: boolean;
  sort: number;
  created_at: string;
  updated_at: string;
}

export type DietaryTag =
  | "vegan" | "vegetarian" | "gluten_free" | "dairy_free" | "halal" | "spicy"
  | "contains_nuts" | "contains_pork" | "contains_shellfish";

export interface MenuItem {
  id: number;
  outlet_id: number;
  sku: string | null;
  category: string | null;
  category_ar: string | null;
  name_en: string;
  name_ar: string | null;
  desc_en: string | null;
  desc_ar: string | null;
  price: string;
  price_minor: number;
  currency: string;
  image_file_id: string | null;
  available_from: string | null;
  available_to: string | null;
  days_mask: number;
  dietary: DietaryTag[];
  alcohol: boolean;
  prep_min: number | null;
  active: boolean;
  sort: number;
  created_at: string;
  updated_at: string;
}

export interface Pair {
  id: number;
  item_id: number;
  suggest_item_id: number;
  kind: "pairing" | "upgrade";
  reason_en: string | null;
  reason_ar: string | null;
  active: boolean;
  sort: number;
}

export interface ImportReport {
  rows: number;
  created: number;
  updated: number;
  errors: { row: number; error: string }[];
  outlets_created: string[];
  dry_run: boolean;
  applied: boolean;
}

// ─── Orders ──────────────────────────────────────────────────────────────

export type OrderStatus = "placed" | "accepted" | "preparing" | "delivered" | "cancelled" | "refunded";
export type Payment = "room_charge" | "cash" | "card" | "complimentary";

export interface OrderLine {
  id: number;
  item_id: number | null;
  name: string;
  name_ar: string | null;
  qty: number;
  unit_price: string;
  line_total: string;
  notes: string | null;
  upsell: boolean;
}

export interface Order {
  id: number;
  order_id: number;
  room: string;
  outlet_id: number | null;
  source: string;
  status: OrderStatus;
  request_id: number | string | null;
  quote_id: string | null;
  price_display: "inclusive" | "plus_plus";
  subtotal: string;
  service_charge: string;
  municipality_fee: string;
  vat: string;
  total: string;
  total_minor: number;
  currency: string;
  payment: Payment | null;
  paid_at: string | null;
  verified_room: boolean;
  notes: string | null;
  language: string | null;
  cancel_reason: string | null;
  status_changed_at: string | null;
  created_at: string;
  lines: OrderLine[];
}

export interface QuoteLine {
  item_id: number;
  name: string;
  qty: number;
  unit_price: string;
  line_total: string;
  notes: string | null;
}

export interface Quote {
  quote_id: string;
  room: string;
  lines: QuoteLine[];
  price_display: "inclusive" | "plus_plus";
  subtotal: string;
  service_charge: string;
  municipality_fee: string;
  vat: string;
  taxes: { code: string; label: string; rate: string; amount: string; amount_minor: number; included: boolean }[];
  total: string;
  total_minor: number;
  currency: string;
  expires_at: string;
  suggestions: { item_id: number; for_item_id: number; kind: string; name: string; reason: string | null; price: string; currency: string }[];
}

// ─── Funnel ──────────────────────────────────────────────────────────────

export type FunnelState = "shown" | "accepted" | "paid" | "fulfilled" | "refunded";

export interface Funnel {
  start: string;
  end: string;
  currency: string;
  states: Record<FunnelState, { count: number; value_minor: number; value: string }>;
  accept_rate: number | null;
  items: {
    item_id: number; name: string; shown: number; accepted: number; paid: number; fulfilled: number; refunded: number;
    accepted_value_minor: number; accepted_value: string; accept_rate: number | null;
  }[];
}
