import type { Metadata } from "next";
import MarketingHomepage from "../MarketingHomepage";
import { HOME_FAQS } from "../lib/faq";
import {
  SITE,
  BRAND,
  jsonLd,
  organizationSchema,
  websiteSchema,
  faqSchema,
} from "../lib/seo";

// /story: the AI Builder Cup deck's story (the problem, the features, how it
// is built, pricing, trust). The site root is the live prototype itself, the
// deck's Final Product Link, which links here.
//
// Server component by design: the interactive UI lives in MarketingHomepage so
// the marketing copy is present in the initial HTML for the crawlers that never
// execute JavaScript (Bing, PerplexityBot and other AI crawlers).

const TITLE = "Clefs: the AI Guest Service Desk for Hotels";
const DESCRIPTION =
  "Guests ask once, in their language, from the QR card in their room. Clefs sends each request to the right free staff member in their language, escalates late jobs, and closes them only with photo proof. Built on Google Cloud with Gemini.";

export const metadata: Metadata = {
  // `absolute` so the root template's "| Clefs" suffix is not appended twice.
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: { canonical: "/story" },
  // A page's openGraph replaces the layout's whole object, so it repeats the
  // site name and locale (the image still comes from app/opengraph-image.jpg).
  openGraph: { title: TITLE, description: DESCRIPTION, url: `${SITE}/story`, siteName: "Clefs", locale: "en_US", type: "website" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

// The homepage tells the AI Builder Cup deck's story (05 spec), so its own
// Organization and SoftwareApplication nodes say what the page says. The
// shared builders in lib/seo.ts keep serving the other pages.
const ORGANIZATION = {
  ...organizationSchema(),
  description:
    "Clefs is an AI guest service desk for hotels. Guests ask by voice or text, in their language, from a room QR code; Clefs dispatches each request to the right staff member in their language and closes it only with photo proof.",
  areaServed: [
    { "@type": "Country", name: "India", identifier: "IN" },
    { "@type": "Country", name: "United Arab Emirates", identifier: "AE" },
  ],
};

const SOFTWARE = {
  "@type": "SoftwareApplication",
  "@id": `${SITE}/#software`,
  name: BRAND.name,
  applicationCategory: "BusinessApplication",
  applicationSubCategory: "Hotel guest service desk",
  operatingSystem: "Web browser",
  url: SITE,
  publisher: { "@id": `${SITE}/#organization` },
  description: DESCRIPTION,
  featureList: [
    "Voice and text from a room QR code, in the guest's language",
    "Answers only from hotel-approved facts",
    "Dispatch to the right free staff member with an ETA",
    "Jobs shown to staff in their own language",
    "Photo proof checked by Gemini vision before closure",
    "Automatic escalation to supervisor, duty manager and GM",
    "Emergency words page the duty manager at once",
    "Service recovery approved by the duty manager",
    "Owner view of response times by team, floor and shift",
  ],
};

// The FAQPage node is built from HOME_FAQS, the same array the page renders as a
// visible accordion. Google requires marked-up FAQ content to be on the page.
const schema = jsonLd([
  ORGANIZATION,
  websiteSchema(),
  SOFTWARE,
  {
    "@type": "WebPage",
    "@id": `${SITE}/story#webpage`,
    url: `${SITE}/story`,
    name: TITLE,
    isPartOf: { "@id": `${SITE}/#website` },
    about: { "@id": `${SITE}/#software` },
    inLanguage: "en",
  },
  faqSchema(HOME_FAQS, "/story"),
]);

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <MarketingHomepage />
    </>
  );
}
