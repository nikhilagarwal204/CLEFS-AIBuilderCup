/**
 * Single source of truth for everything crawlers read about Clefs.
 *
 * clefs.world 308-redirects to www.clefs.world, so every absolute URL emitted
 * anywhere on the site must use the www host or we hand Google two URLs for the
 * same document and split the signal between them.
 */
export const SITE = "https://www.clefs.world";

// WhatsApp is an opt-in channel: the hotel writes first, from the footer link,
// with the message prefilled, so Clefs never opens a chat it was not asked
// for. The number is a founder's line in NEXT_PUBLIC_WHATSAPP_NUMBER (digits
// only, country code first, e.g. 9715XXXXXXXX; anything else is stripped). Read as a
// literal process.env member so Next inlines it at build time, also into the
// client homepage; unset or empty, the URL is undefined and no footer shows it.
const WHATSAPP_DIGITS = (process.env.NEXT_PUBLIC_WHATSAPP_NUMBER ?? "").replace(/\D/g, "");
const WHATSAPP_TEXT = "Hi, please send me the free Booking.com statement check";

export const BRAND = {
  name: "Clefs",
  legalName: "Clefs",
  // Canon 22: "hotels", never "independent hotels only". The same site and demo
  // serve a chain-property GM.
  tagline: "Profit and service control for hotels",
  logo: `${SITE}/logo.jpg`,
  demoUrl: "https://calendly.com/nikhil_agarwal/connect-with-nikhil",
  whatsappUrl: WHATSAPP_DIGITS
    ? `https://wa.me/${WHATSAPP_DIGITS}?text=${encodeURIComponent(WHATSAPP_TEXT)}`
    : undefined,
} as const;

/**
 * "Clefs" collides hard with Les Clefs d'Or, the 4,000-member association of
 * hotel concierges founded in 1929 - same industry, 97 years of authority, and
 * the plural of the French word for "keys" besides. We will not outrank them on
 * the bare word, and chasing it is wasted effort. `sameAs` is how we instead
 * tell Google that THIS Clefs is a distinct software entity: every profile
 * listed here is a corroborating reference that ties the brand to one identity.
 *
 * Fill these in as the profiles go live - an empty array is correctly omitted
 * from the schema rather than emitting a broken `sameAs`.
 */
export const SOCIAL_PROFILES: string[] = [];

/** Organization node, referenced by @id from every other schema on the site. */
export function organizationSchema() {
  return {
    "@type": "Organization",
    "@id": `${SITE}/#organization`,
    name: BRAND.name,
    legalName: BRAND.legalName,
    url: SITE,
    logo: {
      "@type": "ImageObject",
      url: BRAND.logo,
      width: 1024,
      height: 1024,
    },
    description:
      "Clefs finds the profit leaks hotel systems miss. It audits OTA payouts and rate integrity, spots local demand early, and sends the owner a morning brief of actions to approve. It also runs guest service requests with SLAs and an Arabic and English guest assistant.",
    areaServed: { "@type": "Country", name: "United Arab Emirates", identifier: "AE" },
    ...(SOCIAL_PROFILES.length > 0 ? { sameAs: SOCIAL_PROFILES } : {}),
  };
}

export function websiteSchema() {
  return {
    "@type": "WebSite",
    "@id": `${SITE}/#website`,
    url: SITE,
    name: BRAND.name,
    publisher: { "@id": `${SITE}/#organization` },
    inLanguage: "en",
  };
}

export function softwareSchema() {
  return {
    "@type": "SoftwareApplication",
    "@id": `${SITE}/#software`,
    name: BRAND.name,
    applicationCategory: "BusinessApplication",
    applicationSubCategory: "Hotel revenue assurance and operations",
    operatingSystem: "Web browser",
    url: SITE,
    publisher: { "@id": `${SITE}/#organization` },
    description:
      "Profit and service control for hotels in the UAE. Clefs audits OTA payouts and rate integrity against the hotel's own records, watches local demand, and sends the owner a daily brief of evidenced actions to approve, then verifies the value after execution.",
    // "Guest preference memory across stays" returns only when
    // claims.guest_memory is on (Canon 16); see lib/claims.ts.
    featureList: [
      "OTA payout and rate integrity audit",
      "Local demand radar",
      "Owner daily action brief with approvals",
      "Service request control with SLAs",
      "Arabic and English guest assistant",
    ],
    // No price field: a "0" price told crawlers the product is free. Pricing
    // is quoted per hotel in AED (lib/pricing.ts).
    offers: {
      "@type": "Offer",
      priceCurrency: "AED",
      description: "Quoted per hotel in AED. Book a demo for your price.",
    },
  };
}

/**
 * Breadcrumbs give Google the site's shape and render as the path line in the
 * SERP instead of a bare URL. Home is always the first crumb.
 */
export function breadcrumbSchema(trail: { name: string; path: string }[]) {
  return {
    "@type": "BreadcrumbList",
    itemListElement: [{ name: "Home", path: "/" }, ...trail].map((c, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: c.name,
      item: `${SITE}${c.path === "/" ? "" : c.path}`,
    })),
  };
}

export type Faq = { q: string; a: string };

/**
 * Google requires FAQPage markup to describe content the visitor can actually
 * see on the page. Callers MUST render these same questions and answers in the
 * markup - never emit this from a page that does not show them.
 */
export function faqSchema(faqs: Faq[], pagePath: string) {
  return {
    "@type": "FAQPage",
    "@id": `${SITE}${pagePath === "/" ? "" : pagePath}#faq`,
    mainEntity: faqs.map((f) => ({
      "@type": "Question",
      name: f.q,
      acceptedAnswer: { "@type": "Answer", text: f.a },
    })),
  };
}

/** Wraps nodes in the @graph envelope and serialises for a <script> tag. */
export function jsonLd(nodes: object[]) {
  return JSON.stringify({ "@context": "https://schema.org", "@graph": nodes });
}

// ─── Lead magnets (Canon 23) ─────────────────────────────────────────────────

/**
 * The OTA audit (/free-ota-audit) is the primary lead magnet, but its page
 * belongs to the Profit Guard lane and does not exist yet. Until it ships,
 * every primary CTA points at the Google check (the second lead magnet) and
 * the sitemap leaves the audit out, so no button ends in a 404 and search
 * engines are never told to index a missing URL.
 *
 * Profit Guard: set this to true in the same change that adds
 * app/free-ota-audit/page.tsx. scripts/check-claims.mjs fails the build when
 * the flag and the route disagree, in either direction.
 */
export const OTA_AUDIT_LIVE: boolean = true;
export const OTA_AUDIT_PATH = "/free-ota-audit";

/** One CTA: `label` for buttons, `short` for footers and link lists. */
export type LeadCta = { href: string; label: string; short: string };

export const PRIMARY_LEAD: LeadCta = OTA_AUDIT_LIVE
  ? { href: OTA_AUDIT_PATH, label: "Audit one OTA statement", short: "Free OTA audit" }
  : { href: "/google-check", label: "Run the free Google check", short: "Free Google check" };
