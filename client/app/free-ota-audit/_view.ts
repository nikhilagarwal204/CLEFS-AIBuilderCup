/**
 * Pure view model for /free-ota-audit and /free-ota-audit/r/<token>. Type
 * imports only, so __tests__/view.test.mjs can transpile and test it with
 * node:test (the same way scripts/check-claims.mjs loads claims.ts), and the
 * server page can import validToken without pulling in client hooks.
 *
 * Rules it encodes (02 spec 3.11, Canon 23):
 *   - the full breakdown renders only from an unlocked view;
 *   - a guest is shown as initials whatever the server sends (belt and braces:
 *     the server already masks, a regression there must not leak a name);
 *   - only RECOVERABLE findings (L3, L12) reach an AED total; L1, L1b and L2
 *     are lines to confirm, counted and never summed (server "Public classes");
 *   - the AI snapshot always carries the "API simulation" label.
 */
import type {
  AiSnapshot, AuditView, Failure, Finding, FullReport, Money, Teaser,
} from "./_types";

// ─── Limits (mirror public_audit.py so visitors learn before uploading) ──────

// 4 MB (decimal, as the copy says): Vercel refuses a function body over 4.5 MB
// before the API runs, so the browser stops a bigger file before the upload.
export const MAX_BYTES = 4_000_000;
export const MAX_ROWS = 5000;
export const MAX_PDF_PAGES = 10;
export const RETENTION_DAYS = 30;
export const PER_IP_DAILY = 3;
export const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xls,.pdf";
const EXTENSIONS = /\.(csv|tsv|txt|xlsx|xls|pdf)$/i;

/** A reason to refuse the file before sending it, or null. The server sniffs again. */
export function precheckFile(name: string, size: number): string | null {
  if (size <= 0) return "That file is empty.";
  if (size > MAX_BYTES) return "That file is larger than 4 MB. Export a shorter period.";
  if (!EXTENSIONS.test(name)) return "Upload a CSV, Excel (XLSX or XLS) or PDF statement.";
  return null;
}

// ─── Consent wording ─────────────────────────────────────────────────────────

// Byte-identical to CONSENT_TEXTS in server profit_guard/public_audit.py
// (version 2026-09-29), which stores its own copy with each grant: what the
// visitor ticks must be what is recorded. __tests__ compares the two.
export const CONSENT_CONTACT =
  "Clefs may contact me by the email or WhatsApp number I gave about this audit and Clefs Profit Guard. I can ask Clefs to stop at any time.";
export const CONSENT_INDEX =
  "Clefs may include this statement's results, anonymised (no hotel name, no guest data), in the UAE OTA Leakage Index.";
// The lead's own transfer tick (required with the contact tick): the
// contacts are stored in Singapore and emailed to the team through a US
// provider. Byte-identical to CONSENT_TEXTS["lead_transfer_abroad"].
export const CONSENT_LEAD_TRANSFER =
  "My name, email and WhatsApp number are processed outside the UAE by Clefs' sub-processors: stored on Clefs' database in Singapore and sent to the Clefs team through its email provider in the United States.";
// The upload ticks: byte-identical to UPLOAD_CONSENT_TEXTS on the server
// (version 2026-09-29.2). Every format leaves the UAE (the database is in
// Singapore; a PDF is also read by the AI provider in the US), so the
// transfer tick is required for every file: the server refuses an upload
// without it (transfer_consent_required).
export const CONSENT_PROCESSING =
  "Clefs may process this statement to run the audit. It holds guest names and booking numbers: they are reduced to initials in the report, and the file, its rows and the report are deleted after 30 days.";
export const CONSENT_TRANSFER =
  "This statement, guest names and booking numbers included, is processed outside the UAE by Clefs' sub-processors: Clefs stores it on its database in Singapore, and a PDF's full text is also read by Clefs' AI provider in the United States.";

/** A PDF by its name or declared type (the server sniffs the bytes). */
export function isPdfFile(name: string, type = ""): boolean {
  return /\.pdf$/i.test(name) || type === "application/pdf";
}

// ─── Tokens ──────────────────────────────────────────────────────────────────

/** secrets.token_urlsafe(24) is 32 characters; accept a bounded URL-safe range. */
export function validToken(t: string): boolean {
  return /^[A-Za-z0-9_-]{16,128}$/.test(t);
}

// ─── Detectors ───────────────────────────────────────────────────────────────

export type DetectorInfo = { title: string; blurb: string };

/**
 * Where a finding sits in the report, mirroring the server's public classes
 * (public_audit.py, "Public classes"): "found" is RECOVERABLE (L3, L12), the
 * only rows with an AED total; "confirm" is TO_CONFIRM (L1, L1b) and
 * CHECK_CONTRACT (L2), lines to confirm with the hotel; "info" is Agoda's
 * STRUCTURAL row. The detector set is belt and braces: an L1 or L2 is never
 * counted, whatever class a payload carries.
 */
export type ReportSection = "found" | "confirm" | "info";

const NEVER_COUNTED = new Set(["L1", "L1b", "L2"]);

export function sectionOf(f: Pick<Finding, "detector" | "class" | "scenario">): ReportSection {
  if (f.class === "STRUCTURAL" || f.scenario === "agoda_tax_inclusive") return "info";
  return f.class === "RECOVERABLE" && !NEVER_COUNTED.has(f.detector) ? "found" : "confirm";
}

// Agoda's commission on taxes is an L1 on the server, but it reads differently
// (a contract term, not a fee setup), so it gets its own group.
const AGODA_TAX = "L1_AGODA";

// Order is the report's reading order: the money most likely to come back first.
export const DETECTOR_ORDER = ["L3", "L12", "L2", "L1", "L1b", AGODA_TAX] as const;

export const DETECTORS: Record<string, DetectorInfo> = {
  L1: {
    title: "Commission on the municipality fee and Tourism Dirham",
    blurb: "To confirm with you. The check assumed both fees were collected inside the price. If they were, and they are set up as charges on the extranet, the commission on them can be disputed. The statement shows neither, so these lines are not counted.",
  },
  L1b: {
    title: "The same fees, if they are not set up as charges",
    // No date for when the commission stops: when an extranet change takes
    // effect is not something this audit can verify (docs/DEMO.md rule 3).
    blurb: "To confirm with you. The same lines read the other way: if the fees were collected but are not set up as charges, the commission is due under the contract, so the fix is the extranet setting, not a dispute.",
  },
  // Byte-identical to L2_TITLE in server public_audit.py (__tests__ compares).
  L2: {
    title: "Above the percentage this check assumed: check your contract and programme terms",
    blurb: "The line's commission is above a default percentage per OTA, not a market rate; your contract decides. A programme such as Preferred or Visibility Booster raises it by agreement, so these lines are not counted.",
  },
  // The server's amount is pct x (MF + VAT) (public_audit.check_l1): the VAT
  // part is there whether or not the municipality fee was collected.
  [AGODA_TAX]: {
    title: "Commission on the taxes inside Agoda's rate",
    blurb: "For information. Agoda's rate includes taxes by contract (here the municipality fee and VAT), so its commission is worked out on them too. The municipality fee part depends on whether it was collected. It is not counted.",
  },
  L3: {
    title: "Commission on no-shows and cancellations",
    blurb: "The statement marks the booking as a no-show or a cancellation, yet commission was charged. Commission is only due on a fee you actually collected.",
  },
  L12: {
    title: "Refunds without the commission reversed",
    blurb: "A refund reduced the booking, but the commission on the refunded amount was not taken back.",
  },
  L14: {
    title: "Dispute window still open",
    blurb: "No-shows you can still report to the OTA before the deadline.",
  },
};

export function detectorInfo(code: string): DetectorInfo {
  return DETECTORS[code] ?? { title: `Check ${code}`, blurb: "" };
}

// ─── Guest masking ───────────────────────────────────────────────────────────

/**
 * Initials only. "A. M." passes through; anything that still looks like a
 * word ("Ahmed", "AL MANSOURI/AHMED") is reduced to its initials, at most three.
 */
export function maskGuest(g: string | null | undefined): string | null {
  if (g === null || g === undefined) return null;
  const parts = String(g).replace(/[/,]/g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return null;
  const initials = parts.filter((p) => /^\p{L}/u.test(p)).slice(0, 3).map((p) => `${p[0].toUpperCase()}.`);
  return initials.length ? initials.join(" ") : "Guest";
}

// ─── Money and dates ─────────────────────────────────────────────────────────

const formatters = new Map<string, Intl.NumberFormat>();

function currencyFormat(ccy: string): Intl.NumberFormat {
  let f = formatters.get(ccy);
  if (!f) {
    try {
      f = new Intl.NumberFormat("en-AE", { style: "currency", currency: ccy, currencyDisplay: "code" });
    } catch {
      f = new Intl.NumberFormat("en-AE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    formatters.set(ccy, f);
  }
  return f;
}

/** "AED 1,234.50" from the Decimal string the server sends (never from floats of minor units). */
export function money(amount: string | number | null | undefined, ccy: string): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  if (n === null || n === undefined || !Number.isFinite(n)) return "n/a";
  // Intl puts the code first for en-AE; normalise the no-break space.
  return currencyFormat(ccy).format(n).replace(/ /g, " ");
}

export function moneyOf(m: Money | null | undefined, ccy: string): string {
  return m ? money(m.amount, ccy) : "n/a";
}

const DUBAI_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", day: "numeric", month: "short", year: "numeric" });
const DUBAI_WHEN = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Dubai", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});

/** A calendar date ("2026-10-12") or an instant, as a Dubai day. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "";
  // Calendar dates are read at noon UTC so no timezone shift moves them a day.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? iso : DUBAI_DAY.format(d);
}

/** An instant as "12 Oct, 14:00" Dubai time. */
export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${DUBAI_WHEN.format(d)} Dubai time`;
}

/** Whole hours from now to `iso` (negative when past), or null. */
export function hoursLeft(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.floor((t - now) / 3_600_000);
}

// ─── Failures ────────────────────────────────────────────────────────────────

const CODE_FAILURE: Record<string, Failure> = {
  rate_limited: "rate_limited",
  not_found: "not_found",
  file_too_large: "too_large",
  too_many_rows: "too_many_rows",
  too_many_pages: "too_many_pages",
  unsupported_file_type: "unsupported",
  consent_required: "consent",
  transfer_consent_required: "transfer",
  invalid_input: "invalid",
  network_error: "network",
};

/**
 * Map an API error ({status, code}) to a page state. `tokenCall` separates a
 * 404 on a token (expired or unknown audit) from a 404 on the upload, which
 * only means the endpoint is not deployed yet.
 */
export function failureOf(err: { status?: number; code?: string } | null | undefined, tokenCall = false): Failure {
  if (!err) return "unavailable";
  const code = err.code ?? "";
  if (code === "not_found" || err.status === 404) return tokenCall && code === "not_found" ? "not_found" : "unavailable";
  if (CODE_FAILURE[code]) return CODE_FAILURE[code];
  if (err.status === 0) return "network";
  if (err.status === 429) return "rate_limited";
  if (err.status === 413) return "too_large";
  if (err.status === 415) return "unsupported";
  // Every other 422 from the upload is an ingest failure: the file was read
  // but its columns or rows were not usable (pdf_no_text, no_header_row, ...).
  if (err.status === 422) return "unreadable";
  return "unavailable";
}

export const FAILURE_TEXT: Record<Failure, string> = {
  unavailable: "The audit is unavailable right now. Try again later.",
  rate_limited: "You have reached today's limit of three audits. Try again tomorrow.",
  not_found: "This audit has expired or does not exist. Audits are deleted after 30 days; upload the statement again.",
  too_large: "That file is larger than 4 MB. Export a shorter period and upload again.",
  too_many_rows: "That statement has more than 5,000 rows. Export a shorter period and upload again.",
  too_many_pages: "That PDF has more than 10 pages. Export a shorter period, or the CSV or Excel version.",
  unsupported: "That file type is not supported. Upload a CSV, Excel (XLSX or XLS) or PDF statement.",
  unreadable: "We could not read the statement's columns. Try the CSV or Excel export from the extranet, and pick the OTA.",
  consent: "Tick the box that lets Clefs process the statement.",
  transfer: "The statement is stored and processed outside the UAE by Clefs' sub-processors. Tick the box that allows it.",
  invalid: "Some details are missing or look wrong. Check the form and try again.",
  network: "Can't reach Clefs. Check your connection and try again.",
};

// ─── Report ──────────────────────────────────────────────────────────────────

export type DetectorGroup = {
  detector: string;
  section: ReportSection;
  title: string;
  blurb: string;
  count: number;
  total_minor: number;
  /** Sum of the group's findings, formatted; null outside "found": a line to confirm is never summed into a figure. */
  total: string | null;
  findings: Finding[];
};

/** Findings grouped by section and detector in reading order, each group sorted by amount. */
export function groupFindings(findings: Finding[] | null | undefined, ccy: string): DetectorGroup[] {
  const by = new Map<string, { section: ReportSection; detector: string; list: Finding[] }>();
  for (const f of findings ?? []) {
    const section = sectionOf(f);
    const detector = f.scenario === "agoda_tax_inclusive" ? AGODA_TAX : f.detector;
    const key = `${section}|${detector}`;
    const g = by.get(key) ?? { section, detector, list: [] };
    g.list.push({ ...f, guest: maskGuest(f.guest) });
    by.set(key, g);
  }
  const rank = (d: string) => {
    const i = (DETECTOR_ORDER as readonly string[]).indexOf(d);
    return i < 0 ? DETECTOR_ORDER.length : i;
  };
  return [...by.values()]
    .sort((a, b) => rank(a.detector) - rank(b.detector) || a.detector.localeCompare(b.detector))
    .map(({ section, detector, list }) => {
      list.sort((x, y) => y.amount_minor - x.amount_minor || (x.row ?? 0) - (y.row ?? 0));
      const total_minor = list.reduce((s, f) => s + (f.amount_minor || 0), 0);
      const info = detectorInfo(detector);
      return {
        detector, section, title: info.title, blurb: info.blurb, count: list.length, total_minor,
        total: section === "found" ? moneyMinor(total_minor, ccy, list[0]?.amount) : null, findings: list,
      };
    });
}

/**
 * The report's three cards, one per section: the findings that count, the
 * lines to confirm, and the rows shown for information. WHY a card of its
 * own for "info": Agoda's tax-inclusive rate is neither a finding nor a line
 * to confirm, and the teaser counts it as neither. Filed under "To confirm
 * with you", an Agoda-only statement read "No overcharges found" in the
 * teaser and "lines to confirm" in the report.
 */
export function reportCards(groups: DetectorGroup[]): Record<"counted" | "confirm" | "info", DetectorGroup[]> {
  return {
    counted: groups.filter((g) => g.section === "found"),
    confirm: groups.filter((g) => g.section === "confirm"),
    info: groups.filter((g) => g.section === "info"),
  };
}

function decimalsOf(amount: string | undefined): number {
  const m = /\.(\d+)$/.exec(amount ?? "");
  return m ? m[1].length : 2;
}

/**
 * Format an int of minor units. The currency exponent is read from a sibling
 * Decimal string the server sent (`like`, e.g. "300.00"), 2 when there is none,
 * so a sum of amount_minor never goes through float division of the whole.
 */
export function moneyMinor(minor: number, ccy: string, like?: string): string {
  const digits = decimalsOf(like);
  const neg = minor < 0;
  const abs = String(Math.abs(Math.trunc(minor))).padStart(digits + 1, "0");
  const text = digits ? `${abs.slice(0, -digits)}.${abs.slice(-digits)}` : abs;
  return money(`${neg ? "-" : ""}${text}`, ccy);
}

/**
 * The headline figure: "AED 1,200". The server now sends low == high (only
 * RECOVERABLE findings count, L1 no longer widens a range), so a range
 * "AED 800 to AED 1,200" shows only for a payload that still differs.
 */
export function rangeText(t: Teaser): string {
  const ccy = t.currency || "AED";
  const low = t.found?.low;
  const high = t.found?.high;
  if (!low || !high) return "n/a";
  return low.minor === high.minor ? moneyOf(high, ccy) : `${moneyOf(low, ccy)} to ${moneyOf(high, ccy)}`;
}

export const OTA_LABEL: Record<string, string> = { BOOKING: "Booking.com", AGODA: "Agoda", EXPEDIA: "Expedia" };

export function otaLabel(code: string | null | undefined): string {
  return code ? OTA_LABEL[code] ?? code : "OTA not recognised";
}

/**
 * The teaser's per-check counts of findings as rows, in reading order, zero
 * counts dropped. The server counts RECOVERABLE findings only; a payload that
 * still carries L1, L1b or L2 never lists them as findings here (they are
 * lines to confirm, see confirmLines).
 */
export function teaserRows(t: Teaser): { detector: string; title: string; count: number }[] {
  const entries = Object.entries(t.by_detector ?? {}).filter(([d, n]) => n > 0 && !NEVER_COUNTED.has(d));
  const rank = (d: string) => {
    const i = (DETECTOR_ORDER as readonly string[]).indexOf(d);
    return i < 0 ? DETECTOR_ORDER.length : i;
  };
  return entries
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([detector, count]) => ({ detector, title: detectorInfo(detector).title, count }));
}

/** The honest sub-line under any teaser that shows a figure or lines to confirm. */
export const TEASER_SUBLINE = "Each line needs your contract and PMS to confirm; a founder will review it with you.";

/** Statement lines the server marked to confirm (L1, L1b, L2), 0 when the payload has no count. */
export function confirmLines(t: Teaser): number {
  const n = t.to_confirm;
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? Math.trunc(n) : 0;
}

/** "1 line to confirm with you", "58 lines to confirm with you": a count, never an amount. */
export function confirmText(n: number): string {
  return `${n.toLocaleString("en-AE")} ${n === 1 ? "line" : "lines"} to confirm with you`;
}

/**
 * True when the teaser was built under the public classes: the server sends
 * `to_confirm` with every such teaser, 0 included. Only then is `found` an AED
 * figure of counted findings. WHY: web and API are two Vercel projects that
 * deploy separately (docs/DEPLOY.md section 1). A teaser from an API that
 * predates the guard sums L2 into found.low and L1 into found.high, an AED
 * figure nobody checked, so without the count the teaser shows its per-check
 * rows and no figure at all.
 */
export function figureOk(t: Teaser): boolean {
  return typeof t.to_confirm === "number";
}

/**
 * What the teaser leads with. "found": RECOVERABLE findings, with their AED
 * figure when figureOk. "confirm": no figure, only lines to confirm, so the
 * teaser says how many and must not say nothing was found. "nothing": neither.
 */
export function teaserLead(t: Teaser): "found" | "confirm" | "nothing" {
  if (t.findings > 0) return "found";
  return confirmLines(t) > 0 ? "confirm" : "nothing";
}

// ─── Page state ──────────────────────────────────────────────────────────────

export type View =
  | { kind: "loading" }
  | { kind: "failure"; failure: Failure }
  | { kind: "empty" }
  | { kind: "teaser"; teaser: Teaser }
  | { kind: "unlocked"; teaser: Teaser; full: FullReport; groups: DetectorGroup[]; hotel: string | null; snapshot: SnapshotView };

/**
 * The page state. The breakdown and the AI snapshot come ONLY from an
 * unlocked view: a teaser can never render a finding row, a guest or a
 * snapshot answer, whatever else the payload carries.
 */
export function viewOf(audit: AuditView | null, failure: Failure | null): View {
  if (failure) return { kind: "failure", failure };
  if (!audit) return { kind: "loading" };
  const teaser = audit.teaser;
  if (!teaser) return { kind: "empty" };
  if (audit.unlocked && audit.full) {
    const ccy = teaser.currency || "AED";
    return {
      kind: "unlocked", teaser, full: audit.full,
      groups: groupFindings(audit.full.findings, ccy),
      hotel: audit.lead?.hotel_name ?? null,
      snapshot: snapshotOf(audit.ai_snapshot),
    };
  }
  return { kind: "teaser", teaser };
}

// ─── AI snapshot ─────────────────────────────────────────────────────────────

/** The server's label for non-SerpApi engines (visibility ai_radar.LABEL_SIMULATION). */
export const SIMULATION_LABEL = "API simulation: consumer app answers vary";

export type SnapshotView =
  | { kind: "unavailable"; label: string }
  | { kind: "running"; label: string }
  | { kind: "failed"; label: string }
  | {
      kind: "ready";
      label: string;
      engines: { name: string; display: string; label: string }[];
      domains: string[];
      answers: { prompt: string; engine: string; label: string; answer: string; citations: { url: string; title: string }[] }[];
    };

const ENGINE_NAME: Record<string, string> = {
  gemini_web_search: "Gemini (Google Search grounding)",
  perplexity_sonar: "Perplexity Sonar (API)",
  serpapi_ai_mode: "Google AI Mode (SerpApi)",
};

export function engineName(e: string): string {
  return ENGINE_NAME[e] ?? e.replace(/_/g, " ");
}

/**
 * The section label always leads with "API simulation": answers come from the
 * engines' APIs, not the consumer apps a guest uses. A server label that
 * already says so is kept (it may add the SerpApi third-party note).
 */
export function snapshotLabel(label: string | null | undefined): string {
  const l = (label ?? "").trim();
  if (!l) return SIMULATION_LABEL;
  return /api simulation/i.test(l) ? l : `${SIMULATION_LABEL}; ${l}`;
}

function safeUrl(u: unknown): string | null {
  if (typeof u !== "string") return null;
  return /^https?:\/\//i.test(u) ? u : null;
}

export function snapshotOf(s: AiSnapshot | null | undefined): SnapshotView {
  const label = snapshotLabel(s?.label);
  if (!s || s.status === "unavailable") return { kind: "unavailable", label };
  if (s.status === "running" || s.status === "queued") return { kind: "running", label };
  if (s.status === "failed") return { kind: "failed", label };
  const engines = (s.per_engine ?? []).map((e) => ({
    name: engineName(e.engine),
    display: e.display ?? `mentioned in ${e.mentioned} of ${e.runs} answers`,
    label: e.label ?? SIMULATION_LABEL,
  }));
  const answers = (s.answers_about_you ?? []).slice(0, 6).map((a) => ({
    prompt: a.prompt,
    engine: engineName(a.engine),
    label: a.label ?? SIMULATION_LABEL,
    answer: (a.answer ?? "").slice(0, 1200),
    citations: (a.citations ?? [])
      .map((c) => ({ url: safeUrl(c.url), title: (c.title ?? "").trim() }))
      .filter((c): c is { url: string; title: string } => c.url !== null)
      .slice(0, 5),
  }));
  if (!engines.length && !answers.length) return { kind: "unavailable", label };
  return { kind: "ready", label, engines, domains: (s.top_cited_domains ?? []).slice(0, 5), answers };
}

// ─── Leakage Index ───────────────────────────────────────────────────────────

/** "0.0421" -> "4.2%": a ratio string as a percent with one decimal. */
export function rateText(ratio: string | null | undefined): string {
  const x = Number(ratio);
  if (ratio == null || ratio === "" || !Number.isFinite(x)) return "";
  return `${(Math.round(x * 1000) / 10).toFixed(1)}%`;
}

/** Whether the index may render: published by the server AND n at or above
 * its own minimum (belt and braces: a figure never shows below min_n). */
export function indexVisible(ix: { published?: boolean; n?: number; min_n?: number } | null | undefined): boolean {
  return !!ix && ix.published === true && typeof ix.n === "number" && typeof ix.min_n === "number" && ix.n >= ix.min_n;
}

/** Methodology keys in reading order, with their headings. */
export const METHOD_ORDER: [string, string][] = [
  ["source", "Source"], ["measure", "Measure"], ["detectors", "Checks"], ["assumptions", "Assumptions"],
  ["statistic", "Statistic"], ["publication", "Publication rule"], ["limitations", "Limitations"],
];
