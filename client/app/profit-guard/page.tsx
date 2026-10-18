import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { PRIMARY_LEAD, SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/profit-guard";
const TITLE = "Profit Guard: OTA Payout and Rate Integrity Audit";
const DESCRIPTION =
  "Profit Guard checks every Booking.com, Agoda and Expedia statement against your contract, the UAE fee rules and your own reservations, and turns each mismatch into an evidence pack your owner approves.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "article" },
};

// WHY no leakage rate anywhere on this page: we have no verified figure for how
// much UAE hotels lose (Canon 21), so the page describes the checks and lets the
// free audit find the hotel's own number.
const FAQS: Faq[] = [
  {
    q: "Which OTAs does Profit Guard read?",
    a: "Booking.com, Agoda and Expedia statements, as CSV, XLSX or PDF, uploaded in the console or forwarded by email. Columns are matched automatically and a person confirms anything the system is not sure about.",
  },
  {
    q: "What does it compare the statement against?",
    a: "Your contract terms (commission, programme fees and payout rules, which you confirm once), the UAE fee rules that apply to your category and date, and your own reservations export. A line only becomes an alert when those sources disagree.",
  },
  {
    q: "Does Clefs change anything on the extranet?",
    a: "No. Clefs prepares the dispute pack and the rate-parity evidence. Your team or your owner decides whether to send it, and every refund, rate or OTA change always needs an approval.",
  },
  {
    q: "How is recovered money counted?",
    a: "Only when it shows up. A recovery is marked verified when the corrected payout or credit appears on a later statement, and only verified amounts are reported as Clefs-verified profit.",
  },
  {
    q: "Can I try it before connecting anything?",
    a: "Yes. Upload one OTA statement to the free audit and we will show you what the checks find on your own numbers.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Profit Guard", path: PATH }]),
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
        eyebrow="Profit Guard"
        h1="Find the money your OTA statements miss."
        h1Accent="Every payout, checked line by line."
        intro={DESCRIPTION}
        cta={{
          primary: { href: PRIMARY_LEAD.href, label: PRIMARY_LEAD.label },
          secondary: { href: "/pilot", label: "How the pilot works" },
        }}
        sections={[
          {
            h2: "What Profit Guard checks",
            body: [
              "OTA statements are long, and the errors in them are small and repeated. Nobody at the hotel has time to recompute every line against the contract, so small differences are paid out and forgotten.",
              "Profit Guard recomputes each reservation: what the OTA should have charged, what it should have paid you, and which fees and taxes apply on that date. Differences become alerts with the evidence attached.",
            ],
            list: [
              { t: "Commission against contract", d: "The rate and programme fees you agreed, applied to the right base, compared with what was charged." },
              { t: "Payouts against reservations", d: "Stays that are missing, cancelled after arrival, or paid on the wrong dates and amounts." },
              { t: "UAE fees and Tourism Dirham", d: "Service charge, municipality fee, VAT and Tourism Dirham by category, with the convention your contract uses." },
              { t: "Rate integrity", d: "Evidence records when your rate on a channel differs from the rate you loaded, so a parity dispute has dates and screenshots, not memory." },
              { t: "Deadlines", d: "Dispute windows and virtual-card expiry dates counted down, so a claim is not lost to the calendar." },
            ],
          },
          {
            h2: "From alert to money back",
            body: [
              "Each finding carries the statement line, the contract term and the reservation it was checked against. Clefs drafts the dispute pack. Your owner approves it in the morning brief, your team sends it, and the ledger follows it until the credit appears.",
              "Nothing is reported as recovered until it is on a later statement. That is the number we call Clefs-verified profit, and it is the only number we ask you to judge us on.",
            ],
          },
          {
            h2: "What it does not do",
            body: [
              "Profit Guard is not a channel manager and not a revenue management system. It does not load rates, change inventory or talk to guests. It reads what you already receive and checks it.",
            ],
          },
        ]}
        faqs={FAQS}
        related={[
          { href: "/demand-radar", label: "Demand Radar", blurb: "Local events and competitor prices, seen before the week fills up." },
          { href: "/service-control", label: "Service Control", blurb: "Every guest request tracked from asked to done." },
          { href: "/security", label: "Security and data", blurb: "Where statements are stored, who can see them and for how long." },
        ]}
      />
    </>
  );
}
