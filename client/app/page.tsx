import type { Metadata } from "next";
import MarketingHomepage from "./MarketingHomepage";
import { HOME_FAQS } from "./lib/faq";
import { claimToday } from "./lib/claims";
import {
  SITE,
  BRAND,
  jsonLd,
  organizationSchema,
  websiteSchema,
  faqSchema,
} from "./lib/seo";

// The site root is the whole story for AI Builder Cup judges, who only ever
// open the bare URL: the avatar hero, then "Try it yourself" (the live demo
// hotel: be the guest, be Maria, be the manager), then the deck's story,
// pricing, trust and questions. /story and /try redirect here
// (next.config.mjs).
//
// Server component by design: the interactive UI lives in MarketingHomepage,
// and the copy is still in the initial HTML for crawlers that never run
// JavaScript.
//
// The page renders sourced claims (<Claim>), judged against today's date:
// the date is read here, per render, and handed down so the server HTML and
// the hydration render agree. `revalidate` keeps a static build from
// freezing it (check-claims.mjs enforces both).
export const revalidate = 3600;

const TITLE = "Clefs: ask once, in your language, and it gets done";
const DESCRIPTION =
  "Hotel guests ask for anything from their own phone, in their own language, and the right staff member gets it done, with a photo to prove it. Try it live: be the guest, the housekeeper and the manager. Built with Gemini on Google Cloud.";

export const metadata: Metadata = {
  // `absolute` so the root template's "| Clefs" suffix is not appended twice.
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  // A page's openGraph replaces the layout's whole object, so it repeats the
  // site name and locale (the image still comes from app/opengraph-image.jpg).
  openGraph: { title: TITLE, description: DESCRIPTION, url: "/", siteName: "Clefs", locale: "en_US", type: "website" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

// The page tells the AI Builder Cup deck's story, so its own Organization and
// SoftwareApplication nodes say what the page says. The shared builders in
// lib/seo.ts keep serving the other pages.
const ORGANIZATION = {
  ...organizationSchema(),
  description:
    "Clefs is an AI front desk for hotels. Guests ask by voice or text, in their own language, from the QR card in their room; Clefs gives each request to the right staff member, in that person's language, and closes it only with photo proof.",
  // Where the pilots run today (the deck's go-to-market slide).
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
    "Voice and text from a room QR code, in the guest's own language",
    "Answers only from hotel-approved facts",
    "The right free staff member gets each job, and the guest hears when they will arrive",
    "Jobs shown to staff in their own language",
    "Photo proof checked by Gemini vision before a job closes",
    "Late jobs move up to the supervisor, the duty manager and the general manager",
    "Emergency words reach the duty manager at once",
    "An upset guest opens a case the duty manager puts right",
    "Owner view of response times by team, floor and shift",
  ],
};

// The FAQPage node is built from HOME_FAQS, the same array the page renders as
// a visible accordion. Google requires marked-up FAQ content to be on the page.
const schema = jsonLd([
  ORGANIZATION,
  websiteSchema(),
  SOFTWARE,
  {
    "@type": "WebPage",
    "@id": `${SITE}/#webpage`,
    url: SITE,
    name: TITLE,
    isPartOf: { "@id": `${SITE}/#website` },
    about: { "@id": `${SITE}/#software` },
    inLanguage: "en",
  },
  faqSchema(HOME_FAQS, "/"),
]);

export default function Page() {
  const today = claimToday();
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <MarketingHomepage today={today} />
    </>
  );
}
