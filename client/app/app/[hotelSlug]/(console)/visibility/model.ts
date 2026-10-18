/* Pure view model for the visibility console. Type imports only, so
   __tests__/model.test.mjs can transpile and import it with node:test (the
   same trick as google-check/_view.ts): no DOM, no bundler.

   The rules that matter live here, where a test can pin them:
   - an AI cell that fails the minimum-sample gate never shows a number,
     only "insufficient sample (n=...)" (04 spec 5.5, Canon: simulations are
     labelled with n and a CI);
   - the evidence drawer highlights the claimed quote inside the raw answer;
   - setup checklists replace errors on every tab (04 spec 5.11). */

import type { AiCell, AiCitation, Budget, Dec, GbpStatus, SovEntity, VisSettings } from "./types";

// ─── Numbers ─────────────────────────────────────────────────────────────

/** A Decimal string (or number) as a finite number, else null. */
export function num(v: Dec | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** "30%" from a 0..1 ratio; digits default 0 (AI rates are sampled, not exact). */
export function ratioPct(v: Dec | undefined, digits = 0): string {
  const n = num(v);
  return n === null ? "n/a" : `${(n * 100).toFixed(digits)}%`;
}

// ─── Money (format.ts helpers, injected) ─────────────────────────────────

/* WHY injected, not imported: model.ts keeps type-only imports so the
   node:test loader can transpile it alone, while format.ts pulls in React.
   The components pass format.ts's own functions (fmt.money, currencyDigits,
   minorToMajor), so digits, grouping and the currency code all come from
   the one formatter; nothing here hard-codes 2 decimals. */

/** Formatter.money from format.ts (useFormat().money). */
export type MoneyFormat = (amount: number, opts: { currency: string }) => string;

/** format.ts's currencyDigits and minorToMajor. */
export interface MoneyDigits {
  currencyDigits(currency: string): number;
  minorToMajor(minor: number, currency: string): string;
}

/** Vendor costs are micros (K3): USD 1 = 1_000_000 micros. */
export function microsText(micros: number | null | undefined, money: MoneyFormat, currency = "USD"): string {
  if (micros === null || micros === undefined || !Number.isFinite(micros)) return "n/a";
  return money(micros / 1_000_000, { currency });
}

/** The toast after "Draft replies" (04 spec 5.4). Drafts only: the owner
    approves each one in Reviews, so the copy never says "replied". */
export function draftRepliesNotice(out: { drafted: number; considered: number }):
  { tone: "success" | "info" | "error"; title: string; body?: string } {
  if (out.drafted > 0) {
    return { tone: "success", title: `${out.drafted} ${out.drafted === 1 ? "reply" : "replies"} drafted`,
      body: "Review and approve them in Reviews, Replies. Nothing is posted until you approve." };
  }
  if (out.considered === 0) return { tone: "info", title: "No unanswered Google reviews to draft" };
  return { tone: "error", title: "Reviews could not draft replies right now", body: "Try again later." };
}

/** The Google tab's vendor-risk note link (04 spec 5.2, Canon 20), from the
    server's labels.vendor_risk_url. Same-site paths only ("/subprocessors#
    serpapi"): the value lands in an href, so a protocol-relative "//" or a
    "javascript:" value is never rendered. null hides the link. */
export function vendorRiskHref(url: string | null | undefined): string | null {
  return typeof url === "string" && /^\/(?![/\\])[^\s]*$/.test(url) ? url : null;
}

/** An owner-typed major amount ("50", "50.5") as integer minor units with
    `digits` decimals (BHD/KWD/OMR 3, AED/USD 2, JPY 0), by string shifting
    so no float rounding can creep in. null for anything else: a negative or
    malformed amount, or more decimals than the currency has. */
export function majorToMinor(text: string, digits: number): number | null {
  const m = /^(\d+)(?:\.(\d*))?$/.exec(text.trim());
  if (!m) return null;
  const frac = m[2] ?? "";
  if (frac.length > digits) return null;
  const n = Number(m[1] + frac.padEnd(digits, "0"));
  return Number.isSafeInteger(n) ? n : null;
}

/** Spend as a share of the monthly budget, clamped to 0..100 for a bar. */
export function budgetUsedPct(b: Budget | null | undefined): number {
  // Unknown budget (no FX rate): no bar rather than a false "exhausted".
  if (!b || b.budget_micros === null || b.budget_micros === undefined) return 0;
  if (b.budget_micros <= 0) return b.spent_micros > 0 ? 100 : 0;
  return Math.max(0, Math.min(100, (b.spent_micros / b.budget_micros) * 100));
}

// ─── Setup checklists ────────────────────────────────────────────────────

export type SetupTab = "google" | "ai" | "rank" | "gbp" | "site" | "settings";

export interface SetupItem { code: string; label: string; hint: string; where: "settings" | "platform" }

/* Every setup code the routes return, in plain words. "platform" items are
   Clefs-side keys or approvals the hotel cannot fix from Settings. */
const SETUP: Record<string, Omit<SetupItem, "code">> = {
  property_token: { label: "Google Hotels property token", hint: "Paste the property token of your hotel on Google Hotels.", where: "settings" },
  serpapi_enabled: { label: "Turn on Google Hotels checks", hint: "The SerpApi toggle in Settings is off.", where: "settings" },
  serpapi_key: { label: "SerpApi key", hint: "Clefs has not configured the SerpApi key yet.", where: "platform" },
  place_id: { label: "Google place id", hint: "Add your hotel's Google place id.", where: "settings" },
  area: { label: "Area", hint: "Name the area guests search for (for example Dubai Marina).", where: "settings" },
  hotel_location: { label: "Hotel location", hint: "Set the hotel's map location (latitude and longitude) on the main Settings page.", where: "platform" },
  places_api_key: { label: "Places API key", hint: "Clefs has not configured the Google Places key yet.", where: "platform" },
  website_url: { label: "Website URL", hint: "Add the hotel's website address.", where: "settings" },
  google_api_key: { label: "Google API key", hint: "Clefs has not configured the key for Core Web Vitals yet.", where: "platform" },
  no_gemini_key: { label: "Gemini key", hint: "Clefs has not configured the Gemini API key yet.", where: "platform" },
  no_serpapi_key: { label: "SerpApi key", hint: "Google AI Mode needs the SerpApi key.", where: "platform" },
  no_perplexity_key: { label: "Perplexity key", hint: "Perplexity is off until Clefs adds its key.", where: "platform" },
  terms_not_approved: { label: "Perplexity terms review", hint: "Perplexity stays off until its terms pass legal review.", where: "platform" },
  serpapi_disabled: { label: "Turn on SerpApi", hint: "Google AI Mode runs through SerpApi, which is off in Settings.", where: "settings" },
  kill_switch: { label: "AI radar paused", hint: "Clefs has paused the AI radar for all hotels.", where: "platform" },
  engines: { label: "Pick at least one engine", hint: "Turn on an answer engine in Settings.", where: "settings" },
};

export function setupItems(codes: readonly string[] | null | undefined): SetupItem[] {
  const seen = new Set<string>();
  const out: SetupItem[] = [];
  for (const code of codes ?? []) {
    if (seen.has(code)) continue;
    seen.add(code);
    const known = SETUP[code];
    out.push({ code, ...(known ?? { label: code.replace(/_/g, " "), hint: "", where: "platform" as const }) });
  }
  return out;
}

export const GBP_STATE_TEXT: Record<GbpStatus, { label: string; tone: "ok" | "warn" | "neutral"; hint: string }> = {
  project_not_approved: { label: "Project not approved (0 QPM)", tone: "neutral", hint: "Google has not yet approved Clefs' Business Profile API project, so nothing can be read. No action is needed from you." },
  not_connected: { label: "Not connected", tone: "warn", hint: "Connect Google Business Profile. The location must be verified on Google." },
  pick_location: { label: "Pick location", tone: "warn", hint: "Google is connected. Pick which location is this hotel." },
  connected: { label: "Connected", tone: "ok", hint: "Performance metrics sync daily; keywords and KPIs monthly." },
};

// ─── AI answers ──────────────────────────────────────────────────────────

export const ENGINE_LABELS: Record<string, string> = {
  gemini_web_search: "Gemini (Google Search grounding)",
  perplexity_sonar: "Perplexity Sonar",
  serpapi_ai_mode: "Google AI Mode",
};

export function engineLabel(key: string): string {
  return ENGINE_LABELS[key] ?? key.replace(/_/g, " ");
}

export function localeLabel(market: string, language: string): string {
  const lang = language === "ar" ? "Arabic" : language === "en" ? "English" : language.toUpperCase();
  return `${(market || "").trim().toUpperCase() || "Home"} · ${lang}`;
}

export interface CellView {
  sufficient: boolean;
  /** What a person reads: "30% (18-45%)" or "insufficient sample (n=29)". */
  text: string;
  /** Only set when the gate passes, so no caller can render a number below it. */
  rate: number | null;
  low: number | null;
  high: number | null;
  n: number;
}

export function insufficientText(n: number): string {
  return `insufficient sample (n=${n})`;
}

/** The minimum-sample gate is the server's `sufficient` flag; the numbers
    are dropped here when it is false, whatever else the row carries. */
export function cellView(c: Pick<AiCell, "sufficient" | "rate" | "ci_low" | "ci_high" | "runs" | "display">): CellView {
  const n = Number(c.runs) || 0;
  if (!c.sufficient) return { sufficient: false, text: insufficientText(n), rate: null, low: null, high: null, n };
  const rate = num(c.rate), low = num(c.ci_low), high = num(c.ci_high);
  const text = rate === null ? insufficientText(n)
    : low !== null && high !== null ? `${ratioPct(rate)} (${(low * 100).toFixed(0)}-${ratioPct(high)})` : ratioPct(rate);
  return { sufficient: rate !== null, text, rate, low, high, n };
}

export interface SovSegment { key: string; name: string; pct: number; text: string; self: boolean }

/** SoV bar segments, self first, then by share; empty when the cell is below the gate. */
export function sovSegments(entities: readonly SovEntity[], sufficient: boolean): SovSegment[] {
  if (!sufficient) return [];
  const rows = entities
    .map(e => ({ e, v: num(e.sov) }))
    .filter((r): r is { e: SovEntity; v: number } => r.v !== null && r.v > 0);
  rows.sort((a, b) => (a.e.entity === "self" ? -1 : b.e.entity === "self" ? 1 : b.v - a.v));
  return rows.map(({ e, v }) => {
    const lo = num(e.ci_low), hi = num(e.ci_high);
    const ci = lo !== null && hi !== null ? ` (${(lo * 100).toFixed(0)}-${ratioPct(hi)})` : "";
    return { key: e.entity, name: e.name, pct: v * 100, text: `${ratioPct(v)}${ci}`, self: e.entity === "self" };
  });
}

export const SOURCE_CLASS_LABELS: Record<string, string> = {
  ota: "OTAs", review: "Review sites", official: "Your website", google: "Google",
  ugc: "Forums and social", wiki: "Wikipedia", press_other: "Press and other",
};

/** {class: count} as Donut slices (the Donut folds to 5 slices plus Other). */
export function mixSlices(mix: Record<string, number> | null | undefined): { label: string; value: number }[] {
  return Object.entries(mix ?? {})
    .filter(([, v]) => Number(v) > 0)
    .map(([k, v]) => ({ label: SOURCE_CLASS_LABELS[k] ?? k, value: Number(v) }))
    .sort((a, b) => b.value - a.value);
}

export const ERROR_TYPE_TEXT: Record<string, string> = {
  wrong_value: "Wrong value",
  false_absent: "Says you lack it",
  false_closed: "Says it is closed",
  wrong_star: "Wrong star rating",
  price_band_off: "Price band off",
  unverifiable: "Not in your knowledge base",
};

export interface Segment { text: string; mark: boolean }

/** Split an answer so the claimed quote is marked. Case-insensitive and
    whitespace-tolerant; every occurrence is marked; no quote, no marks. */
export function highlight(answer: string | null | undefined, quote: string | null | undefined): Segment[] {
  const text = answer ?? "";
  const q = (quote ?? "").trim();
  if (!text) return [];
  if (q.length < 3) return [{ text, mark: false }];
  const pattern = q.split(/\s+/).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
  const re = new RegExp(pattern, "gi");
  const out: Segment[] = [];
  let at = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > at) out.push({ text: text.slice(at, m.index), mark: false });
    out.push({ text: m[0], mark: true });
    at = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex++;
  }
  if (at < text.length) out.push({ text: text.slice(at), mark: false });
  return out;
}

/** Only http(s) citations become links: an answer is third-party text. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** Citations first (inline, anchored), then search sources; one row per URL. */
export function orderedCitations(cites: readonly AiCitation[]): AiCitation[] {
  const seen = new Set<string>();
  const rank = (c: AiCitation) => (c.kind === "citation" ? 0 : 1);
  return [...cites].sort((a, b) => rank(a) - rank(b)).filter(c => {
    const href = safeHref(c.url);
    if (!href || seen.has(href)) return false;
    seen.add(href);
    return true;
  });
}

// ─── Rank ────────────────────────────────────────────────────────────────

/** SoLV history oldest-first for a Sparkline (the route sends newest last or
    first depending on the query; sorting by time removes the doubt). */
export function solvSeries(history: readonly { run_at: string; solv: Dec }[]): (number | null)[] {
  return [...history]
    .sort((a, b) => Date.parse(a.run_at) - Date.parse(b.run_at))
    .map(h => num(h.solv));
}

export const CALIBRATION_TEXT: Record<string, { label: string; tone: "ok" | "warn" | "neutral" }> = {
  ok: { label: "Calibrated", tone: "ok" },
  drift: { label: "Drift vs Google Maps", tone: "warn" },
  uncalibrated: { label: "Uncalibrated", tone: "neutral" },
};

// ─── Settings ────────────────────────────────────────────────────────────

/** One entry per line (or comma), trimmed, blanks and duplicates dropped. */
export function linesToList(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(/[\n,]/)) {
    const v = part.trim();
    if (v && !out.includes(v)) out.push(v);
  }
  return out;
}

/** "GB London" / "DE" lines to [{market, city?}]; the market is ISO alpha-2. */
export function parseMarkets(text: string): { market: string; city: string | null }[] {
  const out: { market: string; city: string | null }[] = [];
  for (const line of text.split("\n")) {
    const m = line.trim().match(/^([A-Za-z]{2})\b\s*(.*)$/);
    if (!m) continue;
    const market = m[1].toUpperCase();
    if (out.some(o => o.market === market)) continue;
    out.push({ market, city: m[2].trim() || null });
  }
  return out;
}

export function marketsToText(ms: readonly { market: string; city?: string | null }[]): string {
  return ms.map(m => (m.city ? `${m.market} ${m.city}` : m.market)).join("\n");
}

export interface SettingsDraft {
  place_id: string; gh_property_token: string; website_url: string; booking_engine_host: string;
  area: string; landmarks: string; rank_keywords: string; brand_aliases: string; room_page_urls: string;
  free_booking_link_pattern: string; ai_markets: string; grid_size: string; grid_spacing_m: string;
  monthly_budget: string; budget_currency: string; engines_enabled: string[]; serpapi_enabled: boolean;
}

export function draftFrom(s: VisSettings, money: MoneyDigits): SettingsDraft {
  const ccy = s.budget_currency || "USD";
  return {
    place_id: s.place_id ?? "", gh_property_token: s.gh_property_token ?? "", website_url: s.website_url ?? "",
    booking_engine_host: s.booking_engine_host ?? "", area: s.area ?? "", landmarks: (s.landmarks ?? []).join("\n"),
    rank_keywords: (s.rank_keywords ?? []).join("\n"), brand_aliases: (s.brand_aliases ?? []).join("\n"),
    room_page_urls: (s.room_page_urls ?? []).join("\n"), free_booking_link_pattern: s.free_booking_link_pattern ?? "",
    ai_markets: marketsToText(s.ai_markets ?? []), grid_size: String(s.grid_size ?? 7),
    grid_spacing_m: String(s.grid_spacing_m ?? 800),
    // The server reads the minor units with the currency's own digits (money.from_minor).
    monthly_budget: money.minorToMajor(s.monthly_budget_minor ?? 0, ccy), budget_currency: ccy,
    engines_enabled: [...(s.engines_enabled ?? [])], serpapi_enabled: s.serpapi_enabled !== false,
  };
}

const isHttp = (v: string) => /^https?:\/\/[^\s/]+\.[^\s]+$/i.test(v);

/** Client-side mirror of the PUT /settings 422 rules, so errors show inline. */
export function validateDraft(d: SettingsDraft, money: MoneyDigits): Record<string, string> {
  const e: Record<string, string> = {};
  if (d.website_url.trim() && !isHttp(d.website_url.trim())) e.website_url = "Use a full address starting with https://";
  if (linesToList(d.room_page_urls).some(u => !isHttp(u))) e.room_page_urls = "Each room page must start with https://";
  if (linesToList(d.rank_keywords).length > 5) e.rank_keywords = "At most 5 keywords.";
  const g = Number(d.grid_size);
  if (!Number.isInteger(g) || g < 3 || g > 9) e.grid_size = "Grid size is 3 to 9.";
  const sp = Number(d.grid_spacing_m);
  if (!Number.isInteger(sp) || sp < 100 || sp > 5000) e.grid_spacing_m = "Spacing is 100 to 5000 metres.";
  const digits = money.currencyDigits(d.budget_currency.trim().toUpperCase());
  if (majorToMinor(d.monthly_budget, digits) === null) {
    e.monthly_budget = digits ? `Enter an amount of 0 or more, at most ${digits} decimals.` : "Enter a whole amount of 0 or more.";
  }
  if (!/^[A-Za-z]{3}$/.test(d.budget_currency.trim())) e.budget_currency = "A 3-letter currency code.";
  if (linesToList(d.landmarks).length > 10) e.landmarks = "At most 10 landmarks.";
  return e;
}

/** The PUT body. Every field is sent: the route treats the form as the truth. */
export function bodyFrom(d: SettingsDraft, money: MoneyDigits): Record<string, unknown> {
  const ccy = d.budget_currency.trim().toUpperCase();
  const opt = (v: string) => v.trim() || null;
  return {
    place_id: opt(d.place_id), gh_property_token: opt(d.gh_property_token), website_url: opt(d.website_url),
    booking_engine_host: opt(d.booking_engine_host), area: opt(d.area), landmarks: linesToList(d.landmarks),
    rank_keywords: linesToList(d.rank_keywords), brand_aliases: linesToList(d.brand_aliases),
    room_page_urls: linesToList(d.room_page_urls), free_booking_link_pattern: opt(d.free_booking_link_pattern),
    ai_markets: parseMarkets(d.ai_markets), grid_size: Number(d.grid_size), grid_spacing_m: Number(d.grid_spacing_m),
    // Minor units in the budget currency's digits (50 BHD = 50000, 50 JPY = 50);
    // validateDraft has already refused an amount this cannot convert.
    monthly_budget_minor: majorToMinor(d.monthly_budget, money.currencyDigits(ccy)) ?? 0,
    budget_currency: ccy, engines_enabled: d.engines_enabled,
    serpapi_enabled: d.serpapi_enabled,
  };
}

// ─── Dates ───────────────────────────────────────────────────────────────

/** A calendar date ("2026-10-12") pinned to noon UTC, so formatting it in
    any hotel timezone cannot move it to the previous or next day. */
export function noonUtc(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso;
}

// ─── Costs ───────────────────────────────────────────────────────────────

/** The degradation ladder (04 spec 5.10) in plain words. */
export function stepText(step: unknown): string {
  const s = String(step ?? "");
  if (s === "drop_source_markets") return "Dropped the source-market prompts";
  if (s === "repeats_3") return "Cut repeats to 3 per prompt";
  if (s === "drop_ar") return "Dropped the Arabic prompts";
  if (s.startsWith("drop_engine:")) return `Dropped the most expensive engine (${engineLabel(s.slice(12))})`;
  return s.replace(/_/g, " ");
}
