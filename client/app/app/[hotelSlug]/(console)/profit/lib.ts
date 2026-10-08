/* ═══════════════════════════════════════════════════════════════════════
   Profit Guard console helpers: paths, labels, tones and the small bits of
   arithmetic the pages share. Pure functions and type-only imports, so
   `node --test` loads this file directly (profit/__tests__). Money is
   always integer minor units here; formatting happens in components
   through the hotel-bound formatter.
   ═══════════════════════════════════════════════════════════════════════ */

import type { Tone } from "../../../../ui";
import type { Evidence } from "../../../../lib/types";
import type {
  CalcStep, CardStatus, Deadline, DismissReason, EconomicsRow, Finding, FindingClass, FindingStatus, PacketStatus,
  RateEvidence,
} from "./types";

// ─── Paths ───────────────────────────────────────────────────────────────

/** API path under /hotels/{slug}/profit-guard (findings, disputes, imports, ...). */
export const pgApi = (slug: string, p = "") => `/hotels/${encodeURIComponent(slug)}/profit-guard${p}`;

/** The older tax prefix the settings Tax page shares (fee rules, profile). */
export const pgTaxApi = (slug: string, p = "") => `/hotels/${encodeURIComponent(slug)}/profit/tax${p}`;

/** Console route under /app/{slug}/profit. */
export const pgHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/profit${p}`;

// ─── Vocabulary ──────────────────────────────────────────────────────────

/** Owner-facing names; the server's LABELS carry the same meaning. */
export const DETECTOR_LABEL: Record<string, string> = {
  L1: "Commission on taxes and fees",
  L1b: "Fees not set up as charges",
  L2: "Commission rate above contract",
  L3: "Commission on a no-show or cancellation",
  L4: "Commission on unstayed nights",
  L5: "Commission base above the folio",
  L6: "Discount stacking over your cap",
  L7: "Rate parity breach",
  L8: "Virtual card not yet charged",
  L9: "Virtual card expired with balance",
  L10: "Payout shortfall",
  L11: "FX loss on payout",
  L12: "Refund without commission reduction",
  L13: "Chargeback or payment dispute",
  L14: "No-show not yet reported",
  L15: "Reverse-charge VAT on foreign commission",
  T1: "Tourism Dirham under-collected",
  T2: "Tourism Dirham past night 30",
  T3: "Tourism Dirham at the wrong rate",
  T4: "VAT charged on Tourism Dirham",
  T5: "Tourism Dirham charged twice",
  T6: "Tourism Dirham on a no-show",
  T7: "Tourism Dirham remittance gap",
  TX1: "Fee posted inside an exemption",
};

/** The canonical id: upper-case, except the one lower-case suffix (L1b). */
export const detectorKey = (d: string): string => d.toUpperCase().replace(/^L1B$/, "L1b");

export const detectorLabel = (d: string | null | undefined): string =>
  (d && DETECTOR_LABEL[detectorKey(d)]) || d || "Finding";

export const OTA_LABEL: Record<string, string> = {
  BOOKING: "Booking.com",
  AGODA: "Agoda",
  EXPEDIA: "Expedia",
  DIRECT: "Direct",
  DIRECT_WEB: "Direct web",
  GOOGLE_HOTELS: "Google Hotels",
};

export const otaLabel = (code: string | null | undefined): string => {
  if (!code) return "Unknown channel";
  return OTA_LABEL[code.toUpperCase()] ?? titleCase(code);
};

export function titleCase(s: string): string {
  return s.toLowerCase().split(/[_\s]+/).filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join(" ");
}

export const STATUS_ORDER: readonly FindingStatus[] = [
  "DETECTED", "ACCEPTED", "DISPUTED", "RECOVERED", "REJECTED", "UNRESOLVED", "DISMISSED",
];

export const STATUS_LABEL: Record<FindingStatus, string> = {
  DETECTED: "Found", ACCEPTED: "Accepted", DISPUTED: "Disputed", RECOVERED: "Recovered",
  REJECTED: "Rejected", UNRESOLVED: "Unresolved", DISMISSED: "Dismissed",
};

export const STATUS_TONE: Record<FindingStatus, Tone> = {
  DETECTED: "gold", ACCEPTED: "info", DISPUTED: "info", RECOVERED: "ok",
  REJECTED: "bad", UNRESOLVED: "warn", DISMISSED: "neutral",
};

export const OPEN_STATUSES: readonly FindingStatus[] = ["DETECTED", "ACCEPTED", "DISPUTED"];

export const CLASS_LABEL: Record<FindingClass, string> = {
  RECOVERABLE: "Recoverable", PREVENTABLE: "Preventable", STRUCTURAL: "Structural", COMPLIANCE: "Compliance",
};

export const CLASS_TONE: Record<FindingClass, Tone> = {
  RECOVERABLE: "gold", PREVENTABLE: "info", STRUCTURAL: "neutral", COMPLIANCE: "warn",
};

export const DISMISS_REASONS: readonly { value: DismissReason; label: string }[] = [
  { value: "FALSE_POSITIVE_DATA", label: "The data is wrong (false positive)" },
  { value: "CONTRACT_EXCEPTION", label: "Our contract allows this" },
  { value: "ALREADY_HANDLED", label: "Already handled" },
  { value: "TOO_SMALL", label: "Too small to matter" },
  { value: "NOT_WORTH_DISPUTING", label: "Not worth disputing" },
  { value: "OTHER", label: "Other" },
];

export const PACKET_STATUS_LABEL: Record<PacketStatus, string> = {
  DRAFT: "Draft", APPROVED: "Approved", SUBMITTED: "Submitted", ACKNOWLEDGED: "Acknowledged",
  CREDITED: "Credited", PARTIAL: "Partly credited", REJECTED: "Rejected",
};

export const PACKET_STATUS_TONE: Record<PacketStatus, Tone> = {
  DRAFT: "gold", APPROVED: "info", SUBMITTED: "info", ACKNOWLEDGED: "info",
  CREDITED: "ok", PARTIAL: "warn", REJECTED: "bad",
};

export const CARD_STATUS_LABEL: Record<CardStatus, string> = {
  AVAILABLE: "Available", FUNDED: "Funded", NOT_LOADED: "Not loaded", PARTIALLY_CHARGED: "Partly charged",
  FULLY_CHARGED: "Charged", CANCELLED: "Cancelled", EXPIRED: "Expired",
};

export const CHARGEABLE_CARD: readonly CardStatus[] = ["AVAILABLE", "FUNDED", "PARTIALLY_CHARGED"];

// ─── Numbers (null is "not known", never zero) ───────────────────────────

/** A Decimal string or number from the API as a number; null when absent or junk. */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Typed major units ("1,234.5") to integer minor units, by string so no
    float drift reaches the ledger. Null for anything that is not a plain
    non-negative amount with at most `digits` decimals. */
export function majorToMinor(text: string, digits = 2): number | null {
  const t = text.replace(/[\s,]/g, "");
  if (!/^\d+(\.\d*)?$/.test(t)) return null;
  const [whole, frac = ""] = t.split(".");
  if (frac.length > digits) return null;
  const minor = Number(whole) * 10 ** digits + Number((frac + "0".repeat(digits)).slice(0, digits) || "0");
  return Number.isSafeInteger(minor) ? minor : null;
}

/** Minor units back to the Decimal string the server's `amount` fields take. */
export function minorToDecimal(minor: number, digits = 2): string {
  const neg = minor < 0;
  const s = String(Math.abs(Math.trunc(minor))).padStart(digits + 1, "0");
  const out = digits ? `${s.slice(0, -digits)}.${s.slice(-digits)}` : s;
  return neg ? `-${out}` : out;
}

/** Minor units as a major-unit number, for chart axes only. */
export const minorToNumber = (minor: number | null | undefined, digits = 2): number =>
  (minor ?? 0) / 10 ** digits;

// ─── Findings: what a person may do next ─────────────────────────────────

export interface FindingMoves {
  accept: boolean;
  dispute: boolean;
  dismiss: boolean;
  reopen: boolean;
  markRecovered: boolean;
  markRejected: boolean;
}

/** Mirrors ledger.transition and disputes.create_packet, so buttons that
    would only earn a 409 are never offered. The server stays the judge. */
export function findingMoves(f: Pick<Finding, "status" | "class" | "dispute_packet_id">, packetOpen = false): FindingMoves {
  const s = f.status;
  return {
    accept: s === "DETECTED",
    dispute: f.class === "RECOVERABLE" && ["DETECTED", "ACCEPTED", "REJECTED", "UNRESOLVED"].includes(s)
      && !(f.dispute_packet_id && packetOpen),
    dismiss: s === "DETECTED" || s === "ACCEPTED" || s === "DISPUTED",
    reopen: s === "DISMISSED",
    markRecovered: s === "DISPUTED" || s === "UNRESOLVED",
    markRejected: s === "DISPUTED",
  };
}

/** Findings a new packet may take: recoverable, open to dispute, one OTA and one currency. */
export function packetCandidates(rows: readonly Finding[]): { ok: Finding[]; reason: string | null } {
  const ok = rows.filter(r => findingMoves(r).dispute);
  if (!ok.length) return { ok, reason: "Pick recoverable findings that are not already disputed." };
  const otas = new Set(ok.map(r => r.ota_code));
  const ccys = new Set(ok.map(r => r.currency));
  if (otas.size > 1) return { ok, reason: "One dispute packet covers one OTA. Filter by channel first." };
  if (ccys.size > 1) return { ok, reason: "These findings are in different currencies." };
  return { ok, reason: null };
}

/** Still owed on a finding: amount less what was already credited. */
export const owedMinor = (f: Pick<Finding, "amount_minor" | "recovered_minor">): number =>
  Math.max(0, (f.amount_minor ?? 0) - (f.recovered_minor ?? 0));

// ─── Urgency ─────────────────────────────────────────────────────────────

/** Hours to a deadline from `now` (ms); null without one. */
export function hoursUntil(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? (t - now) / 3_600_000 : null;
}

/** 02 spec 3.3 L8 tiers at 30/14/7/1 days: 1 is "act today". */
export function cardTier(daysLeft: number): 1 | 7 | 14 | 30 | null {
  if (daysLeft <= 1) return 1;
  if (daysLeft <= 7) return 7;
  if (daysLeft <= 14) return 14;
  if (daysLeft <= 30) return 30;
  return null;
}

export function cardTierTone(daysLeft: number): Tone {
  const t = cardTier(daysLeft);
  return daysLeft < 0 ? "bad" : t === 1 ? "bad" : t === 7 ? "warn" : t === 14 ? "gold" : "neutral";
}

/** Soonest first; items without a parsable deadline sink to the end. */
export function sortDeadlines<T extends Pick<Deadline, "deadline_at">>(rows: readonly T[]): T[] {
  const at = (r: T) => {
    const t = Date.parse(r.deadline_at ?? "");
    return Number.isFinite(t) ? t : Number.POSITIVE_INFINITY;
  };
  return [...rows].sort((a, b) => at(a) - at(b));
}

// ─── Calculation trace ───────────────────────────────────────────────────

export interface CalcInput {
  key: string;
  label: string;
  /** Integer minor units when the key ends in _minor (format as money). */
  minor: number | null;
  text: string;
}

/** A step's inputs as display rows. `*_minor` keys are money in the
    finding's currency; everything else is shown as the server sent it. */
export function calcInputs(step: Pick<CalcStep, "inputs">): CalcInput[] {
  return Object.entries(step.inputs ?? {}).map(([key, v]) => {
    const isMoney = key.endsWith("_minor");
    const base = isMoney ? key.slice(0, -"_minor".length) : key;
    const minor = isMoney && typeof v === "number" && Number.isFinite(v) ? v : null;
    let text: string;
    if (v === null || v === undefined) text = "n/a";
    else if (typeof v === "object") text = JSON.stringify(v);
    else text = tidyNumber(String(v));
    return { key, label: humanKey(base), minor, text };
  });
}

/** Server Decimals arrive as "18.00000000": show "18" ("0.150" -> "0.15").
    Anything that is not a plain decimal is returned unchanged (QA OWN-13). */
export function tidyNumber(text: string): string {
  return /^-?\d+\.\d+$/.test(text) ? text.replace(/\.?0+$/, "") : text;
}

/** "rooms_booked" -> "Rooms booked", "tol_pct" -> "Tol %", "pct" -> "Percent". */
export function humanKey(key: string): string {
  if (key === "pct") return "Percent";
  const words = key.replace(/_pct$/, " %").replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

/** The last step is the confidence breakdown on detector findings; the
    stepper shows it apart from the money steps. */
export function splitConfidence(steps: readonly CalcStep[]): { money: CalcStep[]; confidence: CalcStep | null } {
  const i = steps.findIndex(s => s.label === "Confidence");
  if (i < 0) return { money: [...steps], confidence: null };
  return { money: steps.filter((_, j) => j !== i), confidence: steps[i] };
}

// ─── Evidence ────────────────────────────────────────────────────────────

const ROW_TABLE_LABEL: Record<string, string> = {
  pfg_ota_statement_lines: "Statement line",
  pfg_ota_statements: "Statement",
  pfg_ota_payout_lines: "Payout line",
  pfg_ota_payouts: "Payout",
  pfg_bank_credits: "Bank credit",
  pfg_virtual_cards: "Virtual card",
  pfg_rate_evidence: "Rate evidence",
  pfg_ota_contracts: "Contract",
  pfg_td_ledger: "Tourism Dirham ledger",
  reservations: "PMS reservation",
  folio_charges: "Folio posting",
};

/** Detector evidence into the kit's EvidencePanel shape. A `row` entry
    becomes readable ("Statement line #42"), and any file it carries (a
    parity screenshot, the statement PDF) becomes its own `file` entry so
    the panel links it. */
export function panelEvidence(evidence: readonly Evidence[] | null | undefined): Evidence[] {
  const out: Evidence[] = [];
  for (const e of evidence ?? []) {
    if (!e || typeof e !== "object") continue;
    const ref = (e.ref ?? {}) as Record<string, unknown>;
    if (e.kind === "row") {
      const table = typeof ref.table === "string" ? ref.table : "";
      const where = `${ROW_TABLE_LABEL[table] ?? (table ? humanKey(table) : "Record")}${ref.id != null ? ` #${String(ref.id)}` : ""}`;
      out.push({ kind: "row", label: e.label || where, value: e.label ? where : undefined, ref });
    } else {
      out.push(e);
    }
    for (const key of ["screenshot_file_id", "file_id"] as const) {
      const fid = ref[key];
      if (e.kind !== "file" && (typeof fid === "string" || typeof fid === "number") && String(fid)) {
        out.push({
          kind: "file",
          label: key === "screenshot_file_id" ? `Screenshot: ${e.label}` : `Source file: ${e.label}`,
          value: key === "screenshot_file_id" ? "Open screenshot" : "Open file",
          ref: { file_id: String(fid), page: typeof ref.page === "number" ? ref.page : undefined },
        });
      }
    }
  }
  return out;
}

// ─── Channel economics ───────────────────────────────────────────────────

const ECON_SUM = [
  "room_nights", "gross_minor", "room_rev_minor", "sc_minor", "pass_through_tax_minor", "commission_minor",
  "commission_on_tax_minor", "payment_cost_minor", "fx_loss_minor", "cancellation_cost_minor", "program_cost_minor",
  "net_minor",
] as const;

export type ChannelTotal = Pick<EconomicsRow, (typeof ECON_SUM)[number] | "channel" | "currency"> & {
  net_adr_minor: number | null;
  gross_adr_minor: number | null;
  retention: number | null;
  sc_as_revenue: boolean;
  waterfall: EconomicsRow["waterfall"];
};

/** Months summed per channel (one currency: the rollup stores the hotel's).
    The waterfall is rebuilt from the sums with the same steps the server
    uses, so a multi-month view reconciles to Gross and Net exactly. */
export function channelTotals(rows: readonly EconomicsRow[]): ChannelTotal[] {
  const by = new Map<string, ChannelTotal>();
  for (const r of rows) {
    const scRow = r.waterfall?.some(s => s.label === "Service charge") ?? false;
    let t = by.get(r.channel);
    if (!t) {
      const zeros = Object.fromEntries(ECON_SUM.map(k => [k, 0])) as Record<(typeof ECON_SUM)[number], number>;
      t = { channel: r.channel, currency: r.currency, net_adr_minor: null, gross_adr_minor: null, retention: null,
            sc_as_revenue: !scRow, waterfall: [], ...zeros };
      by.set(r.channel, t);
    }
    for (const k of ECON_SUM) (t[k] as number) += Number(r[k] ?? 0);
  }
  return [...by.values()].map(t => {
    const rn = t.room_nights;
    const steps: ChannelTotal["waterfall"] = [
      { label: "Gross", kind: "total", value_minor: t.gross_minor },
      { label: "Taxes and fees passed through", kind: "delta", value_minor: -t.pass_through_tax_minor },
    ];
    // SC not counted as revenue is its own step (the server's rule); it closes Gross to Net.
    if (!t.sc_as_revenue) steps.push({ label: "Service charge", kind: "delta", value_minor: -t.sc_minor });
    steps.push(
      { label: "Commission", kind: "delta", value_minor: -t.commission_minor },
      { label: "Payment costs", kind: "delta", value_minor: -t.payment_cost_minor },
      { label: "FX loss", kind: "delta", value_minor: -t.fx_loss_minor },
      { label: "Cancellation costs", kind: "delta", value_minor: -t.cancellation_cost_minor },
      { label: "Net", kind: "total", value_minor: t.net_minor },
    );
    return {
      ...t,
      net_adr_minor: rn ? Math.round(t.net_minor / rn) : null,
      gross_adr_minor: rn ? Math.round(t.gross_minor / rn) : null,
      retention: t.gross_minor ? t.net_minor / t.gross_minor : null,
      waterfall: steps,
    };
  }).sort((a, b) => b.gross_minor - a.gross_minor);
}

/** "2026-09" -> "Sep 2026" without a timezone (a month is a label, not an instant). */
export function monthLabel(key: string, short = false): string {
  const m = /^(\d{4})-(\d{2})/.exec(key);
  if (!m) return key;
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const name = names[Number(m[2]) - 1] ?? m[2];
  return short ? `${name} ${m[1].slice(2)}` : `${name} ${m[1]}`;
}

// ─── Parity grid ─────────────────────────────────────────────────────────

/** The L7 detector's direct channels (l07_parity.DIRECT, plus any DIRECT* code). */
export const DIRECT_CHANNELS: readonly string[] = ["DIRECT", "DIRECT_WEB", "WEBSITE", "BOOKING_ENGINE", "BRAND"];

export const isDirect = (ch: string): boolean => {
  const c = ch.toUpperCase();
  return DIRECT_CHANNELS.includes(c) || c.startsWith("DIRECT");
};

export interface ParityRow {
  key: string;
  stay_date: string;
  los: number;
  adults: number;
  device: RateEvidence["device"];
  login_state: RateEvidence["login_state"];
  room_label: string | null;
  currency: string;
  /** Latest observation per channel. */
  prices: Record<string, RateEvidence>;
  /** Channels priced below the cheapest other channel x (1 - tol%): L7's rule. */
  breaches: string[];
  anchor: "direct" | "others" | null;
}

/** Evidence grouped like-for-like as L7 groups it (the comparable_key:
    date, LOS, adults, room, device and login state; plus currency and
    whether taxes are included), the latest price per channel. An OTA
    breaches when it undercuts the cheapest other channel by more than the
    tolerance; `anchor` says whether a direct price was captured (the
    exposure is measured against it). */
export function parityGrid(rows: readonly RateEvidence[], tolPct: number): ParityRow[] {
  const groups = new Map<string, ParityRow>();
  for (const r of rows) {
    const key = `${r.comparable_key}|${r.currency}|${r.taxes_included === null ? "?" : r.taxes_included ? "incl" : "excl"}`;
    let g = groups.get(key);
    if (!g) {
      g = { key, stay_date: r.stay_date, los: r.los, adults: r.adults, device: r.device, login_state: r.login_state,
            room_label: r.room_label, currency: r.currency, prices: {}, breaches: [], anchor: null };
      groups.set(key, g);
    }
    const had = g.prices[r.channel];
    if (!had || Date.parse(r.captured_at) > Date.parse(had.captured_at)) g.prices[r.channel] = r;
  }
  const factor = 1 - Math.max(0, tolPct) / 100;
  for (const g of groups.values()) {
    const chans = Object.keys(g.prices);
    const direct = chans.filter(isDirect);
    for (const ch of chans) {
      if (isDirect(ch)) continue;
      const refs = chans.filter(c => c !== ch).map(c => g.prices[c].price_minor);
      if (!refs.length) continue;
      if (g.prices[ch].price_minor < Math.min(...refs) * factor) g.breaches.push(ch);
    }
    g.anchor = direct.length ? "direct" : chans.length > 1 ? "others" : null;
  }
  return [...groups.values()].sort((a, b) => a.stay_date.localeCompare(b.stay_date) || a.key.localeCompare(b.key));
}

// ─── Contract rules in words ─────────────────────────────────────────────

const ord = (n: number) => `${n}${n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th"}`;

/** One dispute_rules entry (contracts.py kinds) as a sentence fragment. */
export function disputeRuleText(r: Record<string, unknown>): string {
  const n = (k: string) => num(r[k]);
  const scope = typeof r.applies_to === "string" ? ` (${r.applies_to.replace(/_/g, " ")})` : "";
  switch (r.kind) {
    case "HOURS_AFTER_CHECKOUT": return `${n("hours") ?? n("h") ?? "?"} h after check-out${scope}`;
    case "BUSINESS_DAYS_AFTER_INVOICE": return `${n("days") ?? n("d") ?? "?"} business days after the invoice${scope}`;
    case "DAY_OF_NEXT_MONTH": return `by the ${ord(n("day") ?? 4)} of the next month${scope}`;
    default: return Object.entries(r).map(([k, v]) => `${humanKey(k)} ${String(v)}`).join(", ");
  }
}

/** vcc_rules as a sentence fragment. */
export function vccRuleText(rules: Record<string, unknown> | null | undefined): string {
  const out: string[] = [];
  for (const [k, v] of Object.entries(rules ?? {})) {
    if (k === "charge_within_days_after_checkout") out.push(`charge within ${String(v)} days after check-out`);
    else if (k === "expires_days_after_checkout") out.push(`cards expire ${String(v)} days after check-out`);
    else if (k === "upc_charge_within_h") out.push(`charge within ${String(v)} h of issue (UPC)`);
    else out.push(`${humanKey(k)} ${String(v)}`);
  }
  return out.length ? out.join("; ") : "None";
}
