/**
 * Claims registry: the ONLY place a market statistic may live (Canon 16, 21, K5;
 * 04-growth-modules section 2).
 *
 * WHY a registry instead of copy-edited numbers: the site is sent by the founder
 * straight to GMs and owners, and one "where is that from?" ends a meeting. Every
 * number we show is therefore a row here with its exact quote, publisher, URL,
 * population, period and an expiry (`review_by`). `scripts/check-claims.mjs`
 * fails the build when marketing copy carries a percentage, a currency amount or
 * a multiplier that is not rendered through `<Claim>` / `claimText()` or tagged
 * `// claim: <id>`, and when a claim is BANNED, UNVERIFIED, expired or used on a
 * surface it is not cleared for.
 *
 * CONSTRAINT: this file has NO imports. The linter transpiles it with the
 * TypeScript compiler and imports the result directly, so the registry the page
 * renders and the registry the linter enforces are the same bytes.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

export type ClaimStatus = "VERIFIED" | "SECONDARY" | "UNVERIFIED" | "BANNED" | "PROPRIETARY";

/** Where a claim may be rendered. The linter maps each file to one surface. */
export type ClaimSurface =
  | "home"
  | "faq"
  | "solution_pages"
  | "google_check"
  | "free_ota_audit"
  | "pilot"
  | "seo_meta";

export type Claim = {
  claim_id: string;
  /** The publisher's own words, verbatim. Never paraphrased into the quote. */
  exact_quote: string;
  /** Display value, exactly as rendered (U+2212 for a negative sign). */
  value: string;
  unit: string;
  geography: string;
  population: string;
  period: string;
  publisher: string;
  source_type: "primary" | "secondary";
  url: string;
  /** ISO date the source was last read by a human. */
  retrieved_at: string;
  /** ISO date after which the claim fails the linter until re-checked. */
  review_by: string;
  status: ClaimStatus;
  allowed_surfaces: ClaimSurface[];
  /** Caption shown with the number. States population and period honestly. */
  label: string;
  /** Canon 16 feature flag that must be on before the claim may render. */
  flag?: string;
  /** Tense switch: from `after` (inclusive, ISO date) the label reads `label`. */
  label_after?: { after: string; label: string };
  /** PROPRIETARY data: hidden until the sample reaches `min_n`. */
  sample?: { n: number; min_n: number };
};

/** A statistic we will not use, with the patterns the linter matches in copy. */
export type BannedClaim = {
  claim_id: string;
  reason: string;
  /** Regex sources, matched case-insensitively against visible text. */
  patterns: string[];
};

// ─── Feature flags (Canon 16) ────────────────────────────────────────────────

/**
 * Product claims that only return to the site once the product does the thing.
 * `claims.guest_memory` stays false until repeat-guest recognition is live
 * (guests module); flipping it here is the one switch that re-enables
 * "Remembers your guests" on every surface. Build-time constant on purpose: a
 * runtime toggle would let the marketing site claim a feature the console of
 * the hotel reading it does not have.
 */
export const CLAIM_FLAGS: Readonly<Record<string, boolean>> = {
  "claims.guest_memory": false,
};

export function claimFlag(flag: string): boolean {
  return CLAIM_FLAGS[flag] === true;
}

/**
 * Copy that may only appear when its flag is on. The linter fails any visible
 * occurrence that is not gated: the enclosing object literal carries
 * `flag: "<flag>"`, or the line carries `// claim-flag: <flag>`.
 */
export const FLAGGED_PHRASES: { flag: string; pattern: string }[] = [
  { flag: "claims.guest_memory", pattern: "remembers?\\s+(your\\s+)?guests?" },
  { flag: "claims.guest_memory", pattern: "guest\\s+preference\\s+memory" },
];

// ─── Verified and proprietary claims (Canon 21 + K5) ─────────────────────────

// Sources and wording: docs/specs/research/webinar/claims-factcheck.md,
// zz-critique.md, ai-visibility.md, inquiry-crm.md, local-seo-google.md. All
// were read on 2026-09-28.
const RETRIEVED = "2026-09-28";

const MARKETING: ClaimSurface[] = ["home", "faq", "solution_pages", "free_ota_audit", "pilot", "seo_meta"];

export const CLAIMS: Claim[] = [
  {
    claim_id: "cloudbeds_2025_ota_direct_share",
    exact_quote: "36.6% direct booking rate / 63.4% OTA booking rate",
    value: "63.4% OTA / 36.6% direct",
    unit: "share of bookings",
    geography: "global (180 countries), no UAE breakout",
    population: "independent hotels on Cloudbeds, 90M bookings",
    period: "2025",
    publisher: "Cloudbeds, 2026 State of Independent Hotels",
    source_type: "primary",
    url: "https://www.cloudbeds.com/hospitality-industry-report/",
    retrieved_at: RETRIEVED,
    review_by: "2027-03-31",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "of bookings, independent hotels globally, 2025 data",
  },
  {
    claim_id: "cloudbeds_2025_cancellations",
    exact_quote: "OTA cancellation rate 21.8% vs 10.6% for direct bookings",
    value: "21.8% vs 10.6%",
    unit: "cancellation rate, OTA vs direct",
    geography: "global (180 countries), no UAE breakout",
    population: "independent hotels on Cloudbeds, 90M bookings",
    period: "2025",
    publisher: "Cloudbeds, 2026 State of Independent Hotels",
    source_type: "primary",
    url: "https://www.cloudbeds.com/hospitality-industry-report/",
    retrieved_at: RETRIEVED,
    review_by: "2027-03-31",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "cancellation rate, OTA vs direct bookings, independent hotels globally, 2025",
  },
  {
    claim_id: "jll_uae_revpar_ytd_jun_2026",
    exact_quote: "UAE-wide RevPAR declining 31.8% year-to-date through June",
    value: "−31.8%",
    unit: "RevPAR change, year to date",
    geography: "UAE",
    population: "UAE hotel market",
    period: "January to June 2026",
    publisher: "JLL, UAE Hospitality Market Dynamics Q2 2026",
    source_type: "primary",
    url: "https://www.jll.com/en-ae/insights/market-dynamics/uae-hospitality",
    retrieved_at: RETRIEVED,
    review_by: "2026-12-31",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "UAE RevPAR change, year to date through June 2026 (JLL)",
  },
  {
    claim_id: "skyscanner_uae_ai_confidence_2026",
    exact_quote: "UAE 81% confident using AI for travel planning",
    value: "81%",
    unit: "share of travellers confident using AI",
    geography: "UAE",
    population: "UAE travellers surveyed by OnePoll (22,000 travellers across markets)",
    period: "June to July 2025 fieldwork, Horizons 2026",
    publisher: "Skyscanner, Horizons 2026",
    source_type: "primary",
    url: "https://www.partners.skyscanner.net/news-case-studies/horizons-2026-planning-trends",
    retrieved_at: RETRIEVED,
    review_by: "2027-03-31",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    // Confidence, not usage: the survey never measured how many actually use AI.
    label: "of UAE travellers say they are confident using AI to plan travel (Skyscanner, 2026)",
  },
  {
    claim_id: "microsoft_uae_genai_usage_q2_2026",
    exact_quote: "73.3% of the UAE's working-age population used generative AI",
    value: "73.3%",
    unit: "share of population using generative AI",
    geography: "UAE",
    population: "UAE population aged 15-64",
    period: "Q2 2026",
    publisher: "Microsoft, The continued state of global AI diffusion in 2026",
    source_type: "primary",
    url: "https://blogs.microsoft.com/on-the-issues/2026/09/21/the-continued-state-of-global-ai-diffusion-in-2026/",
    retrieved_at: RETRIEVED,
    review_by: "2027-03-31",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "UAE population aged 15-64 using generative AI, Q2 2026",
  },
  {
    claim_id: "booking_ai_used_2025",
    exact_quote: "Two in three (67%) have already used AI in some aspect of travel",
    value: "67%",
    unit: "share of travellers",
    geography: "global, 33 markets (UAE sampled, no breakout)",
    population: "37,325 consumers",
    period: "2025",
    publisher: "Booking.com, Global AI Sentiment Report",
    source_type: "primary",
    url: "https://news.booking.com/bookingcom-releases-the-global-ai-sentiment-report/",
    retrieved_at: RETRIEVED,
    review_by: "2027-03-31",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "\"have already used AI in some aspect of travel\", global survey, 33 markets, 2025",
  },
  {
    claim_id: "hbr_speed_to_lead_2011",
    exact_quote: "about 7x more likely to qualify the lead when replying within 1 hour",
    value: "7x",
    unit: "likelihood to qualify a lead, reply within 1 hour",
    geography: "United States",
    population: "2,241 US firms",
    period: "2011",
    publisher: "Harvard Business Review, The Short Life of Online Sales Leads",
    source_type: "primary",
    url: "https://hbr.org/2011/03/the-short-life-of-online-sales-leads",
    retrieved_at: RETRIEVED,
    review_by: "2027-09-30",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "more likely to qualify a lead when replying within 1 hour; general sales research, not hotel data; 2,241 US firms, 2011",
  },
  {
    claim_id: "det_2025_inventory",
    exact_quote: "154,264 rooms across 827 establishments",
    value: "154,264 rooms in 827 establishments",
    unit: "hotel inventory",
    geography: "Dubai",
    population: "all DET-classified hotel establishments",
    period: "2025",
    publisher: "Dubai Department of Economy and Tourism, via Dubai Media Office",
    source_type: "primary",
    url: "https://mediaoffice.ae/en/news/2026/february/09-02/dubais-tourism-industry-achieves-third-successive-record-breaking-year",
    retrieved_at: RETRIEVED,
    review_by: "2027-02-28",
    status: "VERIFIED",
    allowed_surfaces: MARKETING,
    label: "Dubai hotel inventory, 2025 (DET)",
  },
  {
    claim_id: "google_third_party_rates_unavailable_2026_09_30",
    exact_quote: "Google third-party rates for hotel ads will be unavailable after September 30, 2026",
    value: "30 Sep 2026",
    unit: "date",
    geography: "global",
    population: "Hotel Ads and Performance Max for Travel Goals campaigns without a Hotel Center feed",
    period: "effective 30 Sep 2026",
    publisher: "Google Hotel Center Help (answer 14310302)",
    source_type: "primary",
    url: "https://support.google.com/hotelprices/answer/14310302",
    retrieved_at: RETRIEVED,
    review_by: "2026-10-31",
    status: "VERIFIED",
    allowed_surfaces: ["google_check", "free_ota_audit", "solution_pages", "faq", "home"],
    label: "Google third-party rates for Hotel Ads and PMax Travel Goals are unavailable after 30 Sep 2026 (Google)",
    label_after: {
      after: "2026-10-01",
      label: "Google third-party rates for Hotel Ads and PMax Travel Goals have been unavailable since 30 Sep 2026 (Google)",
    },
  },
  {
    claim_id: "triptease_free_booking_links_unaffected",
    exact_quote: "free booking links are not affected",
    value: "Free booking links",
    unit: "scope of the change",
    geography: "global",
    population: "Google Hotels free booking links",
    period: "2026",
    publisher: "Triptease",
    source_type: "secondary",
    url: "https://www.triptease.com/resources/google-is-ending-third-party-rates",
    retrieved_at: RETRIEVED,
    review_by: "2026-12-31",
    status: "SECONDARY",
    allowed_surfaces: ["google_check", "free_ota_audit", "solution_pages", "faq", "home"],
    label: "free booking links are not affected (Triptease)",
  },
  // ─── The AI Builder Cup prototype (05 spec; the deck's benchmarks slide) ───
  // Clefs' own measurements, taken on the prototype in scripted tests and
  // published in the deck. The homepage shows each one labelled as measured
  // on the prototype, never as a result a hotel will get: a hotel's own
  // numbers come from its pilot. Re-check them against the next benchmark run.
  {
    claim_id: "clefs_proto_request_understanding",
    exact_quote: "Request understanding 94%",
    value: "94%",
    unit: "share of scripted guest requests understood correctly (intent, items, quantity, urgency)",
    geography: "prototype test, no hotel",
    population: "30 scripted guest requests run on the Clefs prototype",
    period: "October 2026",
    publisher: "Clefs, AI Builder Cup prototype benchmark",
    source_type: "primary",
    url: "/try",
    retrieved_at: "2026-10-19",
    review_by: "2027-01-31",
    status: "PROPRIETARY",
    allowed_surfaces: ["home"],
    label: "of 30 scripted guest requests understood correctly, measured on the prototype",
  },
  {
    claim_id: "clefs_proto_request_splitting",
    exact_quote: "Multi-request splitting 98%",
    value: "98%",
    unit: "share of multi-request messages split into the right separate jobs",
    geography: "prototype test, no hotel",
    population: "scripted multi-request guest messages run on the Clefs prototype",
    period: "October 2026",
    publisher: "Clefs, AI Builder Cup prototype benchmark",
    source_type: "primary",
    url: "/try",
    retrieved_at: "2026-10-19",
    review_by: "2027-01-31",
    status: "PROPRIETARY",
    allowed_surfaces: ["home"],
    label: "of multi-request messages split into the right jobs, measured on the prototype",
  },
  {
    claim_id: "clefs_proto_photo_verification",
    exact_quote: "Photo verification 91%",
    value: "91%",
    unit: "share of proof photos Gemini vision judged correctly (match or mismatch)",
    geography: "prototype test, no hotel",
    population: "labelled matching and non-matching proof photos run on the Clefs prototype",
    period: "October 2026",
    publisher: "Clefs, AI Builder Cup prototype benchmark",
    source_type: "primary",
    url: "/try",
    retrieved_at: "2026-10-19",
    review_by: "2027-01-31",
    status: "PROPRIETARY",
    allowed_surfaces: ["home"],
    label: "of proof photos judged correctly by Gemini vision, measured on the prototype",
  },
  {
    claim_id: "clefs_proto_task_closure",
    exact_quote: "Task closure 60% faster",
    value: "60% faster",
    unit: "time from a guest's request to a closed job",
    geography: "prototype test, no hotel",
    population: "scripted requests run end to end on the Clefs prototype (the deck's benchmark)",
    period: "October 2026",
    publisher: "Clefs, AI Builder Cup prototype benchmark",
    source_type: "primary",
    url: "/try",
    retrieved_at: "2026-10-19",
    review_by: "2027-01-31",
    status: "PROPRIETARY",
    allowed_surfaces: ["home"],
    label: "task closure, measured on the prototype",
  },
  {
    // Not a market statistic: Clefs' own price list (the deck's pricing
    // slide). Registered so the linter can tell a price from an untagged
    // number, and so the price lives in one row with its review date.
    claim_id: "clefs_price_list_2026",
    exact_quote: "USD 3 per room per month, minimum USD 99; text-only USD 1.50 per room; free 30-day pilot",
    value: "USD 3",
    unit: "price per room per month",
    geography: "every market",
    population: "hotels on the Clefs guest service desk",
    period: "2026 price list",
    publisher: "Clefs price list",
    source_type: "primary",
    url: "/#pricing",
    retrieved_at: "2026-10-19",
    review_by: "2027-03-31",
    status: "PROPRIETARY",
    allowed_surfaces: ["home", "faq"],
    label: "per room per month, minimum USD 99 a month",
  },
  {
    // Clefs' own dataset, built from consented OTA audits (Canon 23). Lane C
    // supplies n; the claim is hidden and the linter fails any use until the
    // sample reaches min_n.
    claim_id: "uae_ota_leakage_index",
    exact_quote: "UAE Hotel OTA Leakage Index",
    value: "",
    // What server profit_guard/index.py measures: the counted findings (L3,
    // L12) over the commission charged. No payout or parity check feeds it.
    unit: "median share of OTA commission charged on no-shows, cancellations and unreduced refunds, from statement-only checks",
    geography: "UAE",
    population: "hotels that consented to aggregate use of their Clefs OTA audit",
    period: "rolling 12 months",
    publisher: "Clefs",
    source_type: "primary",
    url: "/free-ota-audit",
    retrieved_at: RETRIEVED,
    review_by: "2027-03-31",
    status: "PROPRIETARY",
    allowed_surfaces: ["home", "free_ota_audit", "solution_pages", "pilot"],
    label: "UAE Hotel OTA Leakage Index (Clefs audits)",
    sample: { n: 0, min_n: 30 },
  },
];

// ─── Banned claims ───────────────────────────────────────────────────────────

// Every webinar statistic in the Context addendum plus the legacy telephone
// numbers the FAQ CLAIM RULE retired. Patterns are deliberately loose: a
// near-miss paraphrase of a banned number is still the banned number.
export const BANNED_CLAIMS: BannedClaim[] = [
  { claim_id: "wttc_8_6m_workforce_gap", reason: "Global hospitality gap misattributed to India; labour framing is wrong for the UAE in 2026.",
    patterns: ["8\\.6\\s?(m|mn|million)\\b"] },
  { claim_id: "discovery_split_46_29_14_11", reason: "No source; SiteMinder 2026 contradicts it.",
    patterns: ["46\\s*%?[\\s,/]+29\\s*%?[\\s,/]+14\\s*%?[\\s,/]+11"] },
  { claim_id: "booking_89_expect_ai_bookings", reason: "Misquote: the report says planning, not bookings.",
    patterns: ["89\\s?%[^.]{0,40}(expect|want)[^.]{0,40}(ai|book)"] },
  { claim_id: "repeat_guests_20_40", reason: "Unsourced vendor-blog range.",
    patterns: ["20\\s?(-|to|–)\\s?40\\s?%"] },
  { claim_id: "rank_41_to_1", reason: "Unverifiable testimonial; rank promises breach Google policy risk.",
    patterns: ["#\\s?41\\s+to\\s+#\\s?1"] },
  { claim_id: "reviews_350_in_a_month", reason: "Review-velocity promise; Google prohibits solicitation surges.",
    patterns: ["\\b350\\s+reviews"] },
  { claim_id: "missed_call_statistics", reason: "Vendor-blog telephone statistics with no primary study.",
    patterns: ["\\$\\s?127\\b", "85\\s?%[^.]{0,30}never\\s+(call\\s+back|retry)", "40\\s?(-|to|–)\\s?60\\s?%[^.]{0,30}unanswered"] },
  { claim_id: "labour_shortage_framing", reason: "UAE 2026 is a margin story; shortage framing is wrong (claims-factcheck).",
    patterns: ["(labou?r|staff(ing)?|workforce|talent)\\s+(shortage|gap)"] },
  { claim_id: "goppar_uplift_tiers", reason: "Outcome promise with no evidence.",
    patterns: ["goppar[^.]{0,40}\\+\\s?(10|25|50)\\s?%"] },
  { claim_id: "tbrc_ai_market_size", reason: "Vanity market-size figure owners do not act on.",
    patterns: ["\\b(20\\.39|26\\.53|75\\.66)\\s?(b|bn|billion)\\b"] },
  { claim_id: "legacy_60_front_desk_calls", reason: "Vendor-blog statistic retired by the FAQ CLAIM RULE.",
    patterns: ["60\\s?%\\s+of\\s+(front\\s+desk\\s+|hotel\\s+)?calls"] },
  { claim_id: "legacy_80_routine_calls", reason: "Came from the same unsourced family.",
    patterns: ["80\\s?%\\s+of\\s+routine\\s+calls"] },
  { claim_id: "legacy_200_400_calls", reason: "Unsourced call-volume range.",
    patterns: ["200\\s?(-|to|–)\\s?400\\s+(inbound\\s+)?calls"] },
];

// ─── Copy rules shared with the linter ───────────────────────────────────────

/** Phrases that fail anywhere in visible copy, case-insensitive (Canon 22, 03 section 6). */
export const FORBIDDEN_COPY: string[] = [
  "no calls",
  "any language",
  "no bugs",
  "replaces staff",
  "independent hotels only",
  "rank #1",
  "guaranteed",
];

/**
 * Outcome promises, matched with or without a number. The research rejects
 * causal AI uplift, rank and review-velocity promises and GOPPAR growth
 * promises, so the words themselves are the violation.
 */
export const OUTCOME_PATTERNS: string[] = [
  "(boost|improve|increase|raise|climb)\\s+(your\\s+)?(google\\s+)?(rank|ranking|position|visibility)",
  "(recommended|ranked)\\s+by\\s+(ai|gemini|perplexity)",
  "more reviews",
  "uplift",
  "increase (direct )?bookings by",
  "goppar\\s*(growth|\\+)",
  "top of (google|maps|gemini)",
  "(رفع|تحسين|زيادة)\\s+(ترتيب|ظهور)",
  "(زيادة|مضاعفة)\\s+الحجوزات",
  "المزيد من التقييمات",
  "(الأول|الأولى|أعلى)\\s+(على|في)\\s+(جوجل|خرائط)",
];

// ─── Lookup and rendering helpers ────────────────────────────────────────────

const BY_ID = new Map(CLAIMS.map((c) => [c.claim_id, c]));

export function getClaim(id: string): Claim | undefined {
  return BY_ID.get(id);
}

/**
 * Today in Dubai as YYYY-MM-DD. Expiry and the tense switch are calendar facts
 * for a UAE reader, so a visitor at 01:00 on 1 Oct Dubai time already sees the
 * past tense even though it is still 30 Sep in UTC.
 */
export function claimToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Dubai", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
}

/** Why a claim may not render right now, or null when it may. */
export function claimBlocker(c: Claim, surface?: ClaimSurface, today = claimToday()): string | null {
  if (c.status === "BANNED" || c.status === "UNVERIFIED") return `status ${c.status}`;
  if (surface && !c.allowed_surfaces.includes(surface)) return `not allowed on ${surface}`;
  if (c.flag && !claimFlag(c.flag)) return `flag ${c.flag} is off`;
  if (c.sample && c.sample.n < c.sample.min_n) return `sample n=${c.sample.n} below ${c.sample.min_n}`;
  // A build made before expiry must not keep showing the number after it: the
  // runtime check mirrors the linter so a stale deploy degrades to silence.
  if (c.review_by < today) return `review_by ${c.review_by} has passed`;
  return null;
}

/** The caption for today, honouring the tense switch. */
export function claimLabel(c: Claim, today = claimToday()): string {
  return c.label_after && today >= c.label_after.after ? c.label_after.label : c.label;
}

/**
 * How value and label combine into one reading:
 *   - "label_only" when the label already states the value (dates, scope);
 *   - "label_first" when the label is a noun phrase ("UAE RevPAR ...: -31.8%");
 *   - "value_first" when the label continues the number ("81% of UAE travellers ...").
 */
export type ClaimOrder = "label_only" | "label_first" | "value_first";

export function claimOrder(c: Claim, today = claimToday()): ClaimOrder {
  const label = claimLabel(c, today);
  if (!c.value || label.toLowerCase().includes(c.value.toLowerCase())) return "label_only";
  return /^[A-Z]/.test(label) ? "label_first" : "value_first";
}

/** "Publisher, period" for footnotes and title attributes. */
export function claimSource(c: Claim): string {
  return `${c.publisher}, ${c.period}`;
}

/**
 * The claim as one plain string (value plus label), or "" when it may not
 * render. Use for attributes, meta text and anywhere JSX is not available.
 */
export function claimText(id: string, surface?: ClaimSurface): string {
  const c = getClaim(id);
  if (!c) return "";
  const today = claimToday();
  if (claimBlocker(c, surface, today)) return "";
  const label = claimLabel(c, today);
  switch (claimOrder(c, today)) {
    case "label_only": return label;
    case "label_first": return `${label}: ${c.value}`;
    default: return `${c.value} ${label}`;
  }
}
