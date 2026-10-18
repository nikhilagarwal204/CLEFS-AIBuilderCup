/* Client mirror of core/tax calc.py for the ConventionPreview, plus the
   rule resolution the preview needs (rules.py active/_rank/winners).
   Why a mirror and not a server call: the preview recomputes on every
   keystroke across three conventions, and Profit Guard's tax API (02 spec
   3.8) has no decompose route. The maths is exact: rates are numeric(18,8)
   strings, so every coefficient is an integer at scale 10^40 (at most five
   chained percentage fees of 8 decimals each) and division happens once, in
   BigInt, with ROUND_HALF_UP. That reproduces the server goldens to the fil
   (A 396.49/39.65/30.53/23.33 on AED 500 with TD 10). */

// ─── Types (list_fee_rules rows) ───

export interface FeeRule {
  id: number;
  rule_key: string | null;
  hotel_slug: string | null;
  emirate: string;
  property_category: string;
  fee_code: string;
  calc_mode: "PERCENT" | "PER_ROOM_NIGHT" | "REMIT_DEFERRAL" | string;
  rate: string;
  currency?: string | null;
  base: string[] | null;
  vat_applies: boolean;
  cap_consecutive_nights: number | null;
  per_bedroom: boolean;
  effective_from: string;
  effective_to: string | null;
  rule_status: "VERIFIED" | "ANNOUNCED_UNVERIFIED" | "OWNER_CONFIGURED" | string;
  label: string | null;
  params: Record<string, unknown> | null;
  source_url: string | null;
  verified_by: string | null;
  verified_at: string | null;
  override: { apply: boolean; effective_from: string | null; effective_to: string | null; note: string | null } | null;
  applies_on?: boolean;
}

export type Convention = "A" | "B" | "C";
export const CONVENTIONS: readonly Convention[] = ["A", "B", "C"];
export const PERCENT_ORDER = ["SC", "MF", "TF", "DEST", "VAT"] as const;
export type Component = "ROOM" | (typeof PERCENT_ORDER)[number];

export interface PercentFee {
  code: string;
  rate: string;
  base: readonly string[];
}

export interface Breakdown {
  convention: Convention;
  /** Gross ex-TD per 1 of R, as a decimal string ("1.23585"). */
  factor: string;
  room: bigint;
  parts: Record<(typeof PERCENT_ORDER)[number], bigint>;
  td: bigint;
  gross: bigint;
}

// ─── Exact decimals ───

// BigInt() calls, not 0n literals: tsconfig targets ES2017, where TS rejects
// bigint literals (the runtime BigInt itself is in every supported browser).
const ZERO = BigInt(0);
const ONE = BigInt(1);
const TWO = BigInt(2);
const TEN = BigInt(10);
const RATE_DP = 8;
const SCALE_DP = RATE_DP * 5;
const SCALE = TEN ** BigInt(SCALE_DP);
const RATE_ONE = TEN ** BigInt(RATE_DP);

/** A decimal string in plain positional form: "1E+1" -> "10", "2.5e-3" -> "0.0025".
    Why: the server serializes rates with str(Decimal.normalize()), and Python
    prints a normalized integral Decimal with an exponent (AED 10 -> "1E+1"),
    which crashed the whole tax page (OWN-06). The shift is done on the digit
    string, never through a float, so no rate loses precision. Throws on
    anything that is not a finite decimal. */
export function plainDecimal(rate: string | number): string {
  const m = /^\s*([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?\s*$/.exec(String(rate));
  if (!m || (!m[2] && !m[3])) throw new Error(`bad rate ${rate}`);
  const exp = m[4] ? Number(m[4]) : 0;
  // A rate is numeric(18,8): an exponent this large is garbage, not a rate.
  if (!Number.isSafeInteger(exp) || Math.abs(exp) > 64) throw new Error(`bad rate ${rate}`);
  const int = m[2] ?? "";
  const digits = int + (m[3] ?? "");
  // Point position counted from the left of `digits`, after the shift.
  const point = int.length + exp;
  const padded = point < 0 ? "0".repeat(-point) + digits : digits.padEnd(point, "0");
  const at = Math.max(point, 0);
  const whole = padded.slice(0, at).replace(/^0+/, "") || "0";
  const frac = padded.slice(at).replace(/0+$/, "");
  const neg = m[1] === "-" && /[1-9]/.test(digits);
  return `${neg ? "-" : ""}${whole}${frac ? `.${frac}` : ""}`;
}

/** "0.07" -> 7000000n at 8 dp; exponent form ("1E+1") is accepted. Throws on non-decimals. */
export function rateUnits(rate: string): bigint {
  const plain = plainDecimal(rate);
  const neg = plain.startsWith("-");
  const [int, fracRaw = ""] = (neg ? plain.slice(1) : plain).split(".");
  const frac = fracRaw.slice(0, RATE_DP).padEnd(RATE_DP, "0");
  const v = BigInt(int || "0") * RATE_ONE + BigInt(frac || "0");
  return neg ? -v : v;
}

/** Rules with every rate in plain form, so each consumer (preview maths, the
    table, Profit Guard's TaxView) sees "10" and never "1E+1". A rate that is
    not a decimal at all is left as sent: rateUnits still refuses it, but one
    bad row must not blank the page before it renders. */
export function normalizeRules<T extends { rate: string }>(rules: readonly T[]): T[] {
  return rules.map(r => {
    try {
      const rate = plainDecimal(r.rate);
      return rate === r.rate ? r : { ...r, rate };
    } catch {
      return r;
    }
  });
}

/** a / b rounded half away from zero (Python ROUND_HALF_UP on the magnitude). */
export function divRound(a: bigint, b: bigint): bigint {
  if (b === ZERO) throw new Error("division by zero");
  const neg = (a < ZERO) !== (b < ZERO);
  const x = a < ZERO ? -a : a;
  const y = b < ZERO ? -b : b;
  const q = (TWO * x + y) / (TWO * y);
  return neg ? -q : q;
}

/** A scaled integer as a trimmed decimal string: (123585n, 5) -> "1.23585". */
export function scaledToString(v: bigint, dp: number): string {
  const neg = v < ZERO;
  let s = (neg ? -v : v).toString().padStart(dp + 1, "0");
  const int = s.slice(0, s.length - dp);
  s = s.slice(s.length - dp).replace(/0+$/, "");
  return `${neg ? "-" : ""}${int}${s ? `.${s}` : ""}`;
}

// ─── Conventions (calc.py apply_convention / coefficients) ───

/** A keeps each rule's base (compound); B puts MF/TF/DEST on [ROOM]; C puts every fee on [ROOM]. */
export function applyConvention(fees: readonly PercentFee[], convention: Convention): PercentFee[] {
  return fees.map(f => {
    let base: readonly string[] = f.base.length ? f.base : ["ROOM"];
    if (convention === "C" || (convention === "B" && (f.code === "MF" || f.code === "TF" || f.code === "DEST"))) {
      base = ["ROOM"];
    }
    return { ...f, base };
  });
}

/** k per component at scale 10^40 (ROOM = 1). A base naming a later component or TD is ignored. */
export function coefficients(fees: readonly PercentFee[]): Record<Component, bigint> {
  const byCode = new Map(fees.map(f => [f.code, f]));
  const k: Partial<Record<Component, bigint>> = { ROOM: SCALE };
  for (const code of PERCENT_ORDER) {
    const fee = byCode.get(code);
    if (!fee) { k[code] = ZERO; continue; }
    let sum = ZERO;
    for (const b of fee.base) {
      const v = k[b as Component];
      if (v !== undefined) sum += v;
    }
    // Exact: sum has at most 4 chained 8-dp rates, so 10^40 still divides cleanly.
    k[code] = (rateUnits(fee.rate) * sum) / RATE_ONE;
  }
  return k as Record<Component, bigint>;
}

export function factor(fees: readonly PercentFee[], convention: Convention): string {
  const k = coefficients(applyConvention(fees, convention));
  const f = Object.values(k).reduce((a, b) => a + b, ZERO);
  return scaledToString(f, SCALE_DP);
}

/** TD for `units` liable unit-nights, in minor units. */
export function tdMinor(tdRate: string | null | undefined, units: number, digits: number): bigint {
  if (!tdRate || units <= 0) return ZERO;
  return divRound(rateUnits(tdRate) * BigInt(Math.trunc(units)) * TEN ** BigInt(digits), RATE_ONE);
}

/** calc.py decompose: TD first, the rest divided by the factor, each fee rounded once, R takes the residual. */
export function decompose(gross: bigint, td: bigint, fees: readonly PercentFee[], convention: Convention): Breakdown {
  const k = coefficients(applyConvention(fees, convention));
  const f = Object.values(k).reduce((a, b) => a + b, ZERO);
  const base = gross - td;
  const parts = {} as Record<(typeof PERCENT_ORDER)[number], bigint>;
  let sum = ZERO;
  for (const c of PERCENT_ORDER) {
    parts[c] = divRound(k[c] * base, f);
    sum += parts[c];
  }
  return { convention, factor: scaledToString(f, SCALE_DP), room: base - sum, parts, td, gross };
}

/** Major-unit text ("500", "1,234.5") to minor units; null when it is not a number. */
export function parseMajor(text: string, digits: number): bigint | null {
  const clean = text.replace(/[,\s]/g, "");
  const m = /^(\d*)(?:\.(\d*))?$/.exec(clean);
  if (!m || (!m[1] && !m[2])) return null;
  const frac = (m[2] ?? "").padEnd(digits + 1, "0");
  const whole = BigInt(m[1] || "0") * TEN ** BigInt(digits) + BigInt(frac.slice(0, digits) || "0");
  // Half-up on the first dropped digit.
  return Number(frac[digits] ?? "0") >= 5 ? whole + ONE : whole;
}

// ─── Rule resolution (rules.py active / _rank / winners) ───

const UNVERIFIED = "ANNOUNCED_UNVERIFIED";

/** Whether a rule applies to the hotel on `on` (ISO date): category, override, window. */
export function active(rule: FeeRule, on: string, category: string | null): boolean {
  if (rule.property_category !== "*" && rule.property_category !== category) return false;
  const applied = rule.override?.apply;
  if (applied === false) return false;
  if (rule.rule_status === UNVERIFIED && applied !== true) return false;
  // An override narrows, never widens, the announced window.
  let start = rule.override?.effective_from || rule.effective_from;
  if (start < rule.effective_from) start = rule.effective_from;
  let end = rule.override?.effective_to || rule.effective_to;
  if (rule.effective_to && (!end || end > rule.effective_to)) end = rule.effective_to;
  return start <= on && (!end || on <= end);
}

function rank(r: FeeRule): (boolean | string | number)[] {
  return [r.rule_status === UNVERIFIED, r.hotel_slug !== null, r.property_category !== "*", r.effective_from, r.id];
}

function outranks(a: FeeRule, b: FeeRule): boolean {
  const x = rank(a);
  const y = rank(b);
  for (let i = 0; i < x.length; i++) {
    if (x[i] === y[i]) continue;
    return x[i] > y[i];
  }
  return false;
}

/** fee_code -> the winning rule on `on`: applied unverified, hotel-owned, category-specific, newest. */
export function winners(rules: readonly FeeRule[], on: string, category: string | null): Map<string, FeeRule> {
  const best = new Map<string, FeeRule>();
  for (const r of rules) {
    if (r.calc_mode !== "PERCENT" && r.calc_mode !== "PER_ROOM_NIGHT") continue;
    if (!active(r, on, category)) continue;
    const cur = best.get(r.fee_code);
    if (!cur || outranks(r, cur)) best.set(r.fee_code, r);
  }
  return best;
}

export interface ResolvedRates {
  fees: PercentFee[];
  tdRate: string | null;
  tdCap: number | null;
  tdPerBedroom: boolean;
  winners: FeeRule[];
}

export function ratesOn(rules: readonly FeeRule[], on: string, category: string | null,
                        tdPerBedroom: boolean | null = null): ResolvedRates {
  const win = winners(rules, on, category);
  const fees: PercentFee[] = [];
  for (const [code, r] of win) {
    if (r.calc_mode === "PERCENT") fees.push({ code, rate: r.rate, base: r.base?.length ? r.base : ["ROOM"] });
  }
  const td = win.get("TD");
  const tdOk = !!td && td.calc_mode === "PER_ROOM_NIGHT";
  return {
    fees,
    tdRate: tdOk ? td.rate : null,
    tdCap: tdOk ? td.cap_consecutive_nights : null,
    tdPerBedroom: tdPerBedroom ?? (tdOk ? !!td.per_bedroom : false),
    winners: [...win.values()],
  };
}

/** Announced-but-unverified rules whose window meets [start, end] (the banner, rules.py notices). */
export function announcementsIn(rules: readonly FeeRule[], start: string, end: string): FeeRule[] {
  return rules.filter(r => r.rule_status === UNVERIFIED && r.effective_from <= end && (!r.effective_to || r.effective_to >= start));
}

/** One group per announcement (params.group, else the rule key): the exemption, the deferral. */
export function announcementGroups(rules: readonly FeeRule[]): { key: string; rules: FeeRule[] }[] {
  const out = new Map<string, FeeRule[]>();
  for (const r of rules) {
    if (r.rule_status !== UNVERIFIED) continue;
    const g = r.params?.group;
    const key = typeof g === "string" && g ? g : r.rule_key || String(r.id);
    out.set(key, [...(out.get(key) ?? []), r]);
  }
  return [...out].map(([key, list]) => ({ key, rules: list }));
}
