/* Amenities HTTP shapes (server/clefs/modules/amenities/README.md). Money is
   a Decimal string in the hotel currency next to *_minor ints; quantities
   are decimal strings ("2", "0.5", "-1"), so every figure is parsed with
   num() from ./lib before it is drawn or summed. */

export type Category = "BATH" | "DENTAL" | "SHAVING" | "SLIPPERS" | "WATER" | "TEA_COFFEE" | "MINIBAR" | "OTHER";
export type Basis = "PER_OCCUPIED_ROOM_NIGHT" | "PER_ARRIVAL" | "PER_GUEST_NIGHT" | "ON_REQUEST";
export type Excise = "NONE" | "SWEETENED" | "ENERGY";
export type LocationKind = "STORE" | "FLOOR_CLOSET" | "CART" | "DEPARTMENT" | "MINIBAR";
export type Shift = "MORNING" | "EVENING" | "NIGHT";
export type Reason = "REPLENISH" | "GUEST_REQUEST" | "VIP" | "DAMAGE" | "RETURN";

// ─── Catalog ─────────────────────────────────────────────────────────────

export interface Item {
  id: number;
  key: string;
  name: string;
  name_en: string;
  name_ar: string | null;
  sku: string | null;
  category: Category;
  uom: string | null;
  basis: Basis;
  std_qty: string | null;
  unit_cost: string | null;
  unit_cost_minor: number | null;
  currency: string;
  excise_category: Excise;
  is_single_use_plastic: boolean;
  sup_ban_ref: string | null;
  aliases: string[];
  request_type: string | null;
  request_type_key: string | null;
  max_qty: number | null;
  chargeable: boolean;
  price: string | null;
  price_minor: number | null;
  needs_review: boolean;
  source: string;
  source_url: string | null;
  active: boolean;
  sort: number | null;
}

/** POST /items and PATCH /items/{id} body (money as a major-unit string). */
export interface ItemInput {
  name_en: string;
  name_ar?: string | null;
  key?: string;
  sku?: string | null;
  category?: Category;
  uom?: string | null;
  basis?: Basis;
  std_qty?: string | null;
  unit_cost?: string | null;
  excise_category?: Excise;
  is_single_use_plastic?: boolean;
  aliases?: string[];
  max_qty?: number | null;
  chargeable?: boolean;
  price?: string | null;
  active?: boolean;
  needs_review?: boolean;
}

export interface Location {
  id: number;
  kind: LocationKind;
  name: string;
  floor: string | null;
  active: boolean;
}

export interface Par {
  location_id: number;
  item_id: number;
  par_qty: string | null;
  days_of_supply: string | number | null;
  safety_stock: string | null;
  lead_days: string | number | null;
  pinned: boolean;
  computed: {
    source?: string;
    daily_usage?: string | number | null;
    suggested_par?: string | number | null;
    as_of?: string | null;
  } | null;
  computed_at: string | null;
  location_name: string;
  location_kind: LocationKind;
  floor: string | null;
  item_key: string;
  item_name: string;
  on_hand: string | null;
  last_counted_at: string | null;
}

// ─── Issuance ────────────────────────────────────────────────────────────

export interface Line {
  id: number;
  issued_at: string;
  business_date: string;
  shift: Shift | null;
  item_id: number;
  item_key: string | null;
  item_name: string | null;
  qty: string;
  to_floor: string | null;
  staff_name: string | null;
  reason: Reason;
  source: string;
  line_cost: string | null;
  currency: string;
  note: string | null;
  voided_at: string | null;
}

export interface IssueLineInput {
  item_id: number;
  qty: number;
  shift?: Shift;
  to_floor?: string;
  staff_name?: string;
  staff_member_id?: number;
  reason?: Reason;
  note?: string;
}

export interface ImportBatch {
  id: number;
  kind: string;
  status: string;
  filename: string | null;
  row_count: number | null;
  gate_status: string | null;
  errors: unknown;
  row_counts?: Partial<Record<"OK" | "WARN" | "ERROR" | "DUP" | "SKIP", number>>;
  commit_result: {
    written?: number;
    costs_written?: number;
    skipped?: number;
    unmatched_rows?: { row_no: number; item: string | null; reason: string }[];
  } | null;
}

// ─── Reports ─────────────────────────────────────────────────────────────

export interface Coverage {
  days: number;
  days_covered: number;
  days_missing_occupancy: number;
}

export interface ItemRow {
  item_id: number;
  key: string;
  name: string;
  category: Category;
  expected: string;
  requested: string;
  issued: string;
  issued_excluded: string;
  variance_units: string;
  variance_pct: string | null;
  issued_value: string | null;
  expected_value: string | null;
  variance_value: string | null;
  variance_minor: number;
}

export interface FloorRow extends Omit<ItemRow, "item_id" | "key" | "name" | "category"> {
  floor: string | null;
}

export interface Overview {
  end: string;
  currency: string;
  variance_7d: string | null;
  variance_7d_minor: number;
  variance_30d: string | null;
  variance_30d_minor: number;
  cpor_30d: { cpor: string | null; expected_cpor: string | null; gap: string | null; orn: number };
  top_leaking: ItemRow[];
  chart: { item_id: number; name: string; requested: string; issued: string; expected: string }[];
  catalog: { active: number; needs_review: number };
  days_covered_30d: number;
  days_missing_occupancy_30d: number;
}

export interface ItemReport extends Coverage {
  group: "item";
  start: string;
  end: string;
  currency: string;
  rows: ItemRow[];
  orn: number;
}

export interface FloorReport extends Coverage {
  group: "floor";
  start: string;
  end: string;
  currency: string;
  rows: FloorRow[];
  orn: number;
}

export interface ShiftReport extends Coverage {
  group: "shift";
  currency: string;
  rows: { shift: string; issued: string; lines: number; issued_value: string | null; share_pct: string | null }[];
  note: string;
}

export interface StaffReport {
  group: "staff";
  currency: string;
  available: boolean;
  reason?: string;
  note: string;
  rows: {
    staff_name: string;
    issued: string;
    issued_value: string | null;
    days: number;
    floors: string[];
    rooms_served: number;
    issued_per_room: string | null;
    share_of_above_expected_pct: string | null;
  }[];
  counter_rotation?: boolean;
  counter_rotation_note?: string | null;
}

export type GroupReport = ItemReport | FloorReport | ShiftReport | StaffReport;

export interface Cpor extends Coverage {
  start: string;
  end: string;
  currency: string;
  orn: number;
  cpor: string | null;
  expected_cpor: string | null;
  gap: string | null;
  issued_value: string | null;
  expected_value: string | null;
  series: { date: string; orn: number; cpor: string | null; expected_cpor: string | null }[];
}

export interface Minibar {
  currency: string;
  has_folio: boolean;
  refills: string;
  billed: string | null;
  unbilled: string | null;
  unbilled_share_pct: string | null;
  unbilled_value: string | null;
  unbilled_minor: number | null;
  billed_value: string | null;
}

export interface Trend {
  item: Item;
  start: string;
  end: string;
  currency: string;
  series: { date: string; expected: string; requested: string; issued: string; variance: string; variance_value: string | null }[];
  by_floor: { floor: string | null; expected: string; issued: string; variance_value: string | null }[];
  by_shift: { shift: string; issued: string }[];
}

export interface Benchmarks {
  cohort_key: string;
  month: string | null;
  min_hotels: number;
  exact_from: number;
  available: boolean;
  reason: string | null;
  rows: {
    category: Category;
    hotels_count: number;
    published: boolean;
    suppressed_reason: string | null;
    p25: string | null;
    p50: string | null;
    p75: string | null;
    band_low: string | null;
    band_high: string | null;
    yours_per_100_orn: string | null;
  }[];
}
