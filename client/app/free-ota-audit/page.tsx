import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "../security/_shell/DocPage";
import AuditUpload from "./AuditUpload";
import LeakageIndex from "./LeakageIndex";
import { OTA_AUDIT_PATH, SITE, breadcrumbSchema, faqSchema, jsonLd, organizationSchema, type Faq } from "../lib/seo";

/**
 * /free-ota-audit: the primary lead magnet (Canon 23, 02 spec 3.11). The only
 * indexable page of the audit; reports live under the tokenised result route
 * (noindex). Server component: every word below is in the initial HTML, the
 * upload form is the one client island.
 *
 * WHY no leakage figure anywhere here: there is no verified rate for how much
 * UAE hotels lose (Canon 21), and the UAE OTA Leakage Index stays hidden until
 * enough consented audits exist. The page describes the checks and lets the
 * visitor's own statement produce the result: an AED figure only for what the
 * statement itself shows (no-shows, cancellations, refunds), and the lines to
 * confirm with the hotel.
 */

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = OTA_AUDIT_PATH;
const TITLE = "Free OTA Statement Audit for UAE Hotels";
const DESCRIPTION =
  "Upload one Booking.com, Agoda or Expedia statement and see, line by line, the commission charged on no-shows, cancellations and refunds, plus the rate and fee lines to confirm with you.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "website" },
};

// ─── FAQ ─────────────────────────────────────────────────────────────────────

const FAQS: Faq[] = [
  {
    q: "Which file do I upload?",
    a: "The commission statement or invoice export for one month, from the Booking.com, Agoda or Expedia extranet. CSV and Excel work best; a PDF works when it has selectable text.",
  },
  {
    q: "Do you need my extranet login?",
    a: "No. The audit reads only the file you upload. Nothing is connected to your extranet or your PMS.",
  },
  {
    q: "Is the total what the OTA owes me?",
    a: "It is an estimate under default assumptions for a Dubai hotel. Your contract, the programmes you joined and your extranet settings decide the real figure, so each finding shows its calculation and what the audit could not see. Commission on the municipality fee and Tourism Dirham, and percentages above a default per OTA, are kept out of the total and listed to confirm with you.",
  },
  {
    q: "What happens to the guest names in the file?",
    a: "They are reduced to initials when the report is built, and the report never shows a full name. The file, its rows and the report are deleted after 30 days.",
  },
  {
    q: "What is the AI snapshot in the report?",
    a: "A short check of how AI assistants describe your hotel, run through their APIs. It is an API simulation: the answers a guest sees in the consumer apps can differ.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Free OTA audit", path: PATH }]),
  {
    "@type": "WebPage",
    "@id": `${SITE}${PATH}#webpage`,
    url: `${SITE}${PATH}`,
    name: TITLE,
    description: DESCRIPTION,
    inLanguage: "en",
  },
  faqSchema(FAQS, PATH),
]);

// ─── Sections ────────────────────────────────────────────────────────────────

const SECTIONS: DocSection[] = [
  {
    id: "checks",
    h2: "What the audit checks",
    body: (
      <ul>
        <li><strong>No-shows and cancellations</strong>: commission charged on a booking the statement itself marks as a no-show or a cancellation, with the dispute deadline when it is still open.</li>
        <li><strong>Refunds</strong>: a refund that reduced the booking without taking back the commission on it.</li>
        <li><strong>Commission rate</strong>: lines charged above a default percentage per OTA, not a market rate; your contract decides. These lines are to confirm with you and are not counted in the total.</li>
        <li><strong>Taxes and fees</strong>: commission worked out on the municipality fee and Tourism Dirham. Whether it applies depends on whether those fees were collected in the price and how they are set up on your extranet, so these lines are to confirm with you and are not counted in the total.</li>
      </ul>
    ),
  },
  {
    id: "how",
    h2: "How it works",
    body: (
      <ol>
        <li>Upload one statement. The next screen shows the amount found, if any, and how many lines to confirm with you.</li>
        <li>Add your name, hotel and an email or WhatsApp number to open the full report: every finding by check, with the booking, the stay, the calculation and the deadline.</li>
        <li>The report also carries a short AI snapshot: how AI assistants describe your hotel today.</li>
      </ol>
    ),
  },
  {
    id: "limits",
    h2: "Assumptions and limits",
    body: (
      <>
        <p>
          The audit has only the statement, so it assumes a Dubai 3-star hotel and the Dubai fee rules, and it
          compares commission with a default percentage per OTA, not a market rate; your contract decides. Every
          finding lists what it could not see, and the report names the checks that need your contract, your PMS
          or your payout and bank statements. Those run in <Link href="/profit-guard">Profit Guard</Link> once a
          hotel is connected.
        </p>
        <p>
          One file of up to 4 MB, 5,000 rows or a PDF of 10 pages. Three audits a day from one connection.
        </p>
      </>
    ),
  },
  {
    id: "data",
    h2: "Your data and consents",
    body: (
      <>
        <p>
          Each consent is a separate tick with one purpose. Two are needed to run the audit: processing the
          statement, and its processing outside the UAE by Clefs&apos; sub-processors (the database is in
          Singapore; a PDF is also read by the AI provider in the United States). Contact about the audit, and
          the same transfer for your contact details, are asked for when you open the full report. Including your
          anonymised results (no hotel name, no guest data) in the UAE OTA Leakage Index is optional and off
          unless you tick it.
        </p>
        <p>
          Guest names are reduced to initials in the report. The file, its rows, the report and the AI
          snapshot are deleted after 30 days. See <Link href="/security">security</Link> and
          the <Link href="/subprocessors">sub-processors</Link>.
        </p>
      </>
    ),
  },
  {
    id: "faq",
    h2: "Questions",
    body: (
      <dl>
        {FAQS.map((f) => (
          <div key={f.q}>
            <dt><strong>{f.q}</strong></dt>
            <dd>{f.a}</dd>
          </div>
        ))}
      </dl>
    ),
  },
];

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <DocPage
        eyebrow="Free OTA audit"
        title="Audit one OTA statement"
        intro={<p>{DESCRIPTION} Free, and the result comes back on the next screen.</p>}
        sections={SECTIONS}
        toc={false}
      >
        <AuditUpload />
        {/* Renders nothing until the index is published (n at or above its minimum). */}
        <LeakageIndex />
      </DocPage>
    </>
  );
}
