import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import type { Section } from "../components/SolutionPage";
import { BRAND, PRIMARY_LEAD, SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";
import { PILOT_DAYS, PRICING_NOTE, pilotPriceText } from "../lib/pricing";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/pilot";
const TITLE = "The Clefs Pilot: 14 Days on Your Own Numbers";
const DESCRIPTION =
  "A free 14-day pilot on your own OTA statements and PMS reports. Success criteria are agreed before Day 1, and the last day ends with a report of Clefs-verified profit your owner can check line by line.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "article" },
};

// The pilot letter's terms (Brief and Profit Guard; Service Control is not
// in the pilot), so no answer below promises staff, rate or request measures.
const FAQS: Faq[] = [
  {
    q: "Who at the hotel needs to be involved?",
    a: "One champion who gets the files and answers questions within a working day: usually the GM, the revenue lead or the front office manager. The owner sees the morning brief and approves actions. No other staff are needed.",
  },
  {
    q: "What counts as success?",
    a: "Whatever we write down with you before Day 1, with a target for each measure: for example OTA commission recovered, or brief actions approved and carried out. The final report scores each criterion against the baseline from Days 1 to 3.",
  },
  {
    q: "What happens after Day 14?",
    a: "You get the report and decide. If you continue, the monthly price written down before Day 1 applies and nothing is reconnected. If you do not, we export or delete your data as you choose.",
  },
  {
    q: "Is my data safe during a pilot?",
    a: "The pilot runs on the same platform as every customer, with the same access controls, audit log and retention rules. The security page explains where data is stored and who processes it.",
  },
];

// WHY the price comes from lib/pricing.ts and is never typed into copy: the
// site shows a number only once the founder has decided one (03 section 6),
// and a literal here would also need a claim tag the linter cannot give it.
const price = pilotPriceText();

// WHY the clock starts at the first import, not at a signature or a call:
// the baseline (Days 1 to 3) can only measure files Clefs has read, so Day 1
// is the day the first file is imported, and every later step counts from it.
const TIMELINE: Section = {
  h2: "How the 14 days run",
  body: [
    `The pilot is ${PILOT_DAYS} days. Day 1 is the day we import your first file, and the morning brief shows results from Day 4, not only at the end.`,
    "Modules: Brief and Profit Guard; Inquiry Guard only if you ask, with its own data consent.",
  ],
  list: [
    { t: "Before Day 1: agree", d: "We write down with you what success means, with a target for each measure, and the monthly price if you continue. Your champion sends the statements and reports listed below; the bank lines follow by Day 5." },
    { t: "Days 1 to 3: baseline", d: "From your first file on, Clefs measures how things run today, before any action is taken, so the report compares like with like." },
    { t: "Days 4 to 13: live", d: "The morning brief goes out every day. Your owner approves, adjusts or ignores each action, and the ledger follows every approved one to its result. The bank lines arrive by Day 5, so OTA payouts can be matched to the bank." },
    { t: "Day 14: report", d: "Clefs-verified profit, each success criterion against its baseline, what is still pending, and the caveats, with every figure traceable to its source." },
  ],
};

// The pilot letter's list, item for item. Names, card numbers and Remarks are
// deleted or masked by the hotel before a file leaves it: no check needs them.
const INPUTS: Section = {
  h2: "What the hotel provides",
  body: ["Each file is an export from systems you already use, with names and Remarks deleted and card numbers masked before it is sent."],
  list: [
    { t: "Three months of Booking.com reservation statements", d: "As Excel, with the guest-name, booker-name and Remarks columns deleted. Agoda and Expedia statements are optional." },
    { t: "The PMS no-show and cancellation report", d: "For the same months, with the name column deleted." },
    { t: "The payout or virtual-card report", d: "With card numbers masked to the last 4 digits." },
    { t: "Bank lines for OTA receipts only", d: "The date, amount and reference of each OTA receipt, by Day 5." },
    { t: "One champion", d: "A named person who answers questions within a working day." },
  ],
};

const TERMS: Section = {
  h2: "Criteria and conversion, agreed upfront",
  body: [
    price ? `Pilot price: ${price}. ${PRICING_NOTE}` : PRICING_NOTE,
    "We also write down the modules in the pilot, a target for each success criterion and the baseline period. The pilot scorecard in your console tracks the same criteria every morning.",
    "Only verified results count. A recovered payout counts when it appears on a later statement; an approved action counts when the ledger shows it was carried out and measured.",
  ],
};

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Pilot", path: PATH }]),
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
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <SolutionPage
        eyebrow="Pilot"
        h1="Fourteen days on your own numbers."
        h1Accent="Judged by criteria you set."
        intro={DESCRIPTION}
        cta={{
          primary: { href: BRAND.demoUrl, label: "Plan a pilot", external: true },
          secondary: { href: PRIMARY_LEAD.href, label: `${PRIMARY_LEAD.label} first` },
        }}
        sections={[TIMELINE, INPUTS, TERMS]}
        faqs={FAQS}
        related={[
          { href: "/profit-guard", label: "Profit Guard", blurb: "OTA payouts and rate integrity, checked line by line." },
          { href: "/demand-radar", label: "Demand Radar", blurb: "Local events and competitor prices, seen early." },
          { href: "/security", label: "Security and data", blurb: "Where your data lives, who can see it and for how long." },
        ]}
      />
    </>
  );
}
