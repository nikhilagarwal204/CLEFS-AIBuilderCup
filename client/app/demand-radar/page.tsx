import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { PRIMARY_LEAD, SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";
import { claimText } from "../lib/claims";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/demand-radar";
const TITLE = "Demand Radar: Local Events and Competitor Prices for UAE Hotels";
const DESCRIPTION =
  "Demand Radar watches events, holidays and competitor prices around your hotel, and tells your owner which dates need a rate decision before the week fills up.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "article" },
};

const FAQS: Faq[] = [
  {
    q: "Where do the events come from?",
    a: "A curated UAE calendar of venues and public holidays, event data providers, ticketing listings, calendars you share with us, and AI web search that must cite its source. Duplicates are merged and each event keeps its change history, so a rescheduled or cancelled event is marked as such.",
  },
  {
    q: "Where do competitor prices come from?",
    a: "Google Hotels prices through a third-party service, plus any rate-shopping export you already pay for. Prices are labelled indicative: they show direction and gaps, not the exact rate a guest will pay.",
  },
  {
    q: "Does Demand Radar change my rates?",
    a: "No. It recommends a rate range inside the minimum and maximum you set, and your owner or revenue lead approves it. Rate changes always need an approval.",
  },
  {
    q: "How do I know a recommendation was right?",
    a: "Each approved recommendation is followed up after the date passes, against your own pickup and pace, and the result is shown next to the recommendation.",
  },
];

// Dated claims are judged by today's Dubai date, so this page must not be
// frozen at `next build`: it is regenerated at most an hour after the tense
// switch (label_after) or the expiry (review_by), whenever it was deployed.
export const revalidate = 3600;

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Demand Radar", path: PATH }]),
  {
    "@type": "WebPage",
    "@id": `${SITE}${PATH}#webpage`,
    url: `${SITE}${PATH}`,
    name: TITLE,
    description: DESCRIPTION,
    about: { "@id": `${SITE}/#software` },
    inLanguage: "en",
  },
  faqSchema(FAQS, PATH),
]);

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Page() {
  // The Google change is a dated fact from the claims registry (surface
  // solution_pages). Read per render, never at module scope (a module-scope
  // call runs once per build). claimText() returns "" once the claim may not
  // render, and the paragraph is dropped instead of showing a sentence with a
  // hole in it.
  const googleNote = claimText("google_third_party_rates_unavailable_2026_09_30", "solution_pages");
  const freeLinks = claimText("triptease_free_booking_links_unaffected", "solution_pages");
  const googleParas = [
    googleNote && freeLinks ? `${googleNote}; ${freeLinks}.` : "",
    "Demand Radar includes a Hotel Center readiness checklist, because only a direct feed puts an official-site price next to the OTAs on Google.",
  ].filter(Boolean);

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <SolutionPage
        eyebrow="Demand Radar"
        h1="See the next 30 days of demand around your hotel."
        h1Accent="Before it becomes obvious."
        intro={DESCRIPTION}
        cta={{
          primary: { href: "/demand-preview", label: "See demand around my hotel" },
          secondary: { href: PRIMARY_LEAD.href, label: PRIMARY_LEAD.label },
        }}
        sections={[
          {
            h2: "What it watches",
            body: [
              "Exhibitions, concerts, sports, conferences and public holidays move demand in the UAE, often in one corridor and not the next. Demand Radar maps each event to the distance bands that matter for your hotel and learns which kinds you dismiss.",
            ],
            list: [
              { t: "Events near you", d: "Curated venues and holidays, event data providers and cited web search, with rescheduled and cancelled events marked." },
              { t: "Competitor prices", d: "Your comp set on Google Hotels and any rate-shopping export you already use, labelled indicative." },
              { t: "Your own pace", d: "On-the-books and pickup for the same dates, so a recommendation starts from what you have already sold." },
              { t: "A 30, 60 and 90 day view", d: "A heatmap of the dates that need attention, and the top five alerts each week." },
            ],
          },
          {
            h2: "From signal to decision",
            body: [
              "Each alert names the dates, the evidence (the event, the competitor move, your pace) and a rate range inside the guardrails you set. It arrives in the morning brief with one-tap approve, adjust or ignore.",
              "From the same event Clefs can draft a pre-arrival offer, housekeeping staffing guidance and a guest FAQ entry about transport and timings. Each draft waits for approval.",
            ],
          },
          { h2: "Google free booking links", body: googleParas },
        ]}
        faqs={FAQS}
        related={[
          { href: "/profit-guard", label: "Profit Guard", blurb: "OTA payouts and rate integrity, checked line by line." },
          { href: "/google-check", label: "Google parity check", blurb: "Is your official site price showing on Google? Check in a minute." },
          { href: "/service-control", label: "Service Control", blurb: "Every guest request tracked from asked to done." },
        ]}
      />
    </>
  );
}
