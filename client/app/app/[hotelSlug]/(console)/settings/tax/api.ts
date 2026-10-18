/* Tax API paths and payloads. core/tax mounts no routes (its README): the
   owner-facing tax API belongs to Profit Guard under its module prefix
   (02 spec 3.8: GET /tax/fee-rules, GET|PUT /tax/profile, POST
   /tax/overrides), each a thin wrapper over the core function named below.
   A 404 from these paths means Profit Guard is off or not deployed yet; the
   page says so instead of failing. */

import { hotelPath } from "../../../../../lib/api";

export const taxPath = (slug: string, p = "") => hotelPath(slug, `/profit/tax${p}`);

/** core.tax.get_tax_profile */
export interface TaxProfile {
  property_category: string | null;
  convention: "A" | "B" | "C" | null;
  trn: string | null;
  td_per_bedroom: boolean | null;
  effective_from: string | null;
  emirate: string | null;
  currency: string | null;
  source: "profile" | "hotel" | string | null;
}

/** Body of PUT /tax/profile (core.tax.set_tax_profile). */
export interface TaxProfileBody {
  effective_from: string;
  property_category: string;
  convention: "A" | "B" | "C";
  trn: string | null;
  td_per_bedroom: boolean | null;
}

/** Body of POST /tax/overrides (core.tax.set_override). Both ids are sent:
    the core function takes either, so the route may read whichever it names. */
export interface OverrideBody {
  fee_rule_id: number;
  rule_key: string | null;
  apply: boolean;
  effective_from?: string | null;
  effective_to?: string | null;
  note?: string | null;
}

/* Mirrors core/tax models.PROPERTY_CATEGORIES (Dubai Tourism Dirham schedule). */
export const PROPERTY_CATEGORIES: readonly { value: string; label: string }[] = [
  { value: "hotel_5", label: "Hotel, 5 star" },
  { value: "hotel_4", label: "Hotel, 4 star" },
  { value: "hotel_3", label: "Hotel, 3 star" },
  { value: "hotel_2", label: "Hotel, 2 star" },
  { value: "hotel_1", label: "Hotel, 1 star" },
  { value: "apartment_luxury", label: "Hotel apartment, luxury" },
  { value: "apartment_deluxe", label: "Hotel apartment, deluxe" },
  { value: "apartment_standard", label: "Hotel apartment, standard" },
  { value: "guest_house", label: "Guest house" },
  { value: "holiday_home_luxury", label: "Holiday home, luxury" },
  { value: "holiday_home_standard", label: "Holiday home, standard" },
];

export function categoryLabel(v: string | null | undefined): string {
  if (!v) return "Not set";
  if (v === "*") return "All categories";
  return PROPERTY_CATEGORIES.find(c => c.value === v)?.label ?? v;
}

export const FEE_LABEL: Record<string, string> = {
  SC: "Service charge",
  MF: "Municipality fee",
  TF: "Tourism fee",
  TD: "Tourism Dirham fee",
  VAT: "VAT",
  DEST: "Destination fee",
};

export const CONVENTION_HELP: Record<"A" | "B" | "C", string> = {
  A: "Compound: municipality fee on room plus service charge, VAT on everything. Dubai's published method.",
  B: "Municipality and tourism fees on the room rate only; VAT still on everything.",
  C: "Additive: every fee and VAT on the room rate only.",
};
