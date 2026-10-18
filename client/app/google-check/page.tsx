import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "../security/_shell/DocPage";
import Claim from "../components/Claim";
import GoogleCheck from "./GoogleCheck";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema } from "../lib/seo";

/**
 * /google-check: the secondary lead magnet (Canon 23, 04 section 2.4). The
 * only indexable page of the check; results live at /google-check/r/<token>
 * (noindex, disallowed in robots.ts).
 */

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/google-check";

// The sections below render dated claims (<Claim> judges expiry and tense by
// today's Dubai date at render time). Without this the page is prerendered
// once at `next build` and keeps a stale tense or an expired claim for as
// long as that deploy lives; with it the page is regenerated hourly.
export const revalidate = 3600;
const TITLE = "Google Free Booking Link and Parity Check";
const DESCRIPTION =
  "Check in a minute whether your official site price shows on Google Hotels next to the OTAs, and whether the prices match.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "website" },
};

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Google check", path: PATH }]),
  {
    "@type": "WebPage",
    "@id": `${SITE}${PATH}#webpage`,
    url: `${SITE}${PATH}`,
    name: TITLE,
    description: DESCRIPTION,
    inLanguage: "en",
  },
]);

// ─── Sections ────────────────────────────────────────────────────────────────

const SECTIONS: DocSection[] = [
  {
    id: "what",
    h2: "What the check looks at",
    body: (
      <ul>
        <li><strong>Official site price</strong>: whether Google Hotels shows a price from your own website for two upcoming stays.</li>
        <li><strong>Parity</strong>: your official price next to each OTA price Google shows for the same dates.</li>
        <li><strong>Organic position</strong>: where your hotel appears on those dates, with sponsored results removed.</li>
      </ul>
    ),
  },
  {
    id: "why",
    h2: "Why it matters now",
    body: (
      <>
        <p>
          <Claim id="google_third_party_rates_unavailable_2026_09_30" surface="google_check" />;{" "}
          <Claim id="triptease_free_booking_links_unaffected" surface="google_check" />.
        </p>
        <p>
          A free booking link is how your own website price appears next to the OTAs on Google. If it is
          missing, guests who search for your hotel see only OTA prices. Demand Radar includes a Hotel Center
          readiness checklist for hotels that want to fix it.
        </p>
      </>
    ),
  },
  {
    id: "data",
    h2: "Where the data comes from",
    body: (
      <>
        <p>
          Google Hotels data is fetched through SerpApi, a third-party service. Prices are indicative and
          checked at one moment for specific dates; Google and the OTAs may show different prices minutes
          later. The position is the organic position on those dates, not a ranking guarantee.
        </p>
        <p>
          We keep a check for 30 days. Your contact details are used only to send and follow up on this
          check, on the channels you tick. See <Link href="/security">security</Link> and
          the <Link href="/subprocessors">sub-processors</Link>.
        </p>
      </>
    ),
  },
];

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <DocPage
        eyebrow="Google check"
        title="Is your official price showing on Google?"
        intro={<p>{DESCRIPTION} Free, and the answer comes back on the next screen.</p>}
        sections={SECTIONS}
        toc={false}
      >
        <GoogleCheck />
      </DocPage>
    </>
  );
}
