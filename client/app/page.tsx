import type { Metadata } from "next";
import MarketingHomepage from "./MarketingHomepage";
import { claimToday } from "./lib/claims";
import { HOME_FAQS } from "./lib/faq";
import {
  SITE,
  jsonLd,
  organizationSchema,
  websiteSchema,
  softwareSchema,
  faqSchema,
} from "./lib/seo";

// Server component by design: the interactive UI lives in MarketingHomepage so
// the marketing copy is present in the initial HTML for the crawlers that never
// execute JavaScript (Bing, PerplexityBot, ClaudeBot).
export const metadata: Metadata = {
  // `absolute` so the root template's "| Clefs" suffix is not appended twice.
  title: { absolute: "Hotel OTA Audit and Profit Control in the UAE - Clefs" },
  description:
    "Find the profit leaks your hotel systems miss. Clefs audits OTA payouts and rate integrity, spots local demand early, and sends your owner the actions to approve every morning.",
  alternates: { canonical: "/" },
};

// The FAQPage node is built from HOME_FAQS, the same array the page renders as a
// visible accordion. Google requires marked-up FAQ content to be on the page.
const schema = jsonLd([
  organizationSchema(),
  websiteSchema(),
  softwareSchema(),
  {
    "@type": "WebPage",
    "@id": `${SITE}/#webpage`,
    url: SITE,
    name: "Profit and Service Control for Hotels | Clefs",
    isPartOf: { "@id": `${SITE}/#website` },
    about: { "@id": `${SITE}/#software` },
    inLanguage: "en",
  },
  faqSchema(HOME_FAQS, "/"),
]);

// Dated registry claims render on the homepage (the proof column). Regenerate
// hourly so their expiry and tense follow the calendar rather than the build.
export const revalidate = 3600;

export default function Page() {
  // Read once per (re)render on the server and handed to the client tree, so
  // the server HTML and the hydration render judge every claim by the same
  // date: a visitor's clock can never produce a hydration mismatch.
  const claimDate = claimToday();
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <MarketingHomepage claimDate={claimDate} />
    </>
  );
}
