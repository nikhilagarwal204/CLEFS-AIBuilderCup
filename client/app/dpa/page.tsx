import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "../security/_shell/DocPage";
import { RESIDENCY } from "../lib/subprocessors";
import { BRAND, SITE, jsonLd, organizationSchema, breadcrumbSchema } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/dpa";
const TITLE = "Data Processing Agreement";
const DESCRIPTION =
  "The terms on which Clefs processes personal data for hotels: roles, instructions, security, sub-processors, transfers, guest rights, incidents and the end of the contract.";
const UPDATED = "2026-09-29";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "article" },
};

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Security", path: "/security" }, { name: TITLE, path: PATH }]),
]);

// WHY a plain-language summary and not the full legal text: the signed DPA is
// part of each hotel's contract and is reviewed by counsel against the UAE
// PDPL executive regulations. This page states the commitments that document
// makes, so a GM can check them before the lawyers are involved.
const p = (...lines: string[]) => lines.map((l) => <p key={l.slice(0, 32)}>{l}</p>);

// ─── Sections ────────────────────────────────────────────────────────────────

const SECTIONS: DocSection[] = [
  {
    id: "roles",
    h2: "Who is responsible for what",
    body: p(
      "The hotel is the controller of its guests' and staff's personal data: it decides why the data is processed. Clefs is the processor: it processes that data only to run the services the hotel has chosen, and only on the hotel's documented instructions.",
      "Where Clefs collects data for itself, such as an enquiry through this website or the free OTA audit, Clefs is the controller of that data and the privacy notice shown at that point applies.",
    ),
  },
  {
    id: "instructions",
    h2: "Processing only on instructions",
    body: p(
      "Clefs uses hotel data only to provide the services in the contract. It does not sell it, does not use it for advertising, and does not use one hotel's guest data to serve another hotel.",
      "Anonymized, aggregated benchmarks are produced only for hotels that opt in, and a hotel only ever sees its own position.",
    ),
  },
  {
    id: "security",
    h2: "Security measures",
    body: (
      <>
        <p>
          Role-based access with masking of guest details, an append-only audit log verified every night,
          encryption in transit, separate consent records, and automatic retention limits. The measures are
          described on the <Link href="/security">security page</Link>.
        </p>
        <p>Everyone at Clefs with access to hotel data is bound by confidentiality.</p>
      </>
    ),
  },
  {
    id: "subprocessors",
    h2: "Sub-processors",
    body: (
      <p>
        The hotel authorises the providers on the <Link href="/subprocessors">sub-processors page</Link>.
        Clefs gives notice before adding or replacing a provider that receives personal data, and the hotel
        may object. Clefs remains responsible for its sub-processors.
      </p>
    ),
  },
  {
    id: "transfers",
    h2: "Transfers outside the UAE",
    body: p(
      RESIDENCY.statement,
      "Transfers rely on the guest's explicit consent, collected before the assistant starts, and on the contractual safeguards with each provider.",
    ),
  },
  {
    id: "rights",
    h2: "Helping with guest rights",
    body: (
      <p>
        Clefs gives the hotel the tools to answer requests to access, correct, erase, restrict, object or
        move data: a request queue with due dates, data bundles for access and portability, and erasure
        that anonymizes operational records. Guests can use the <Link href="/privacy/request">privacy
        request form</Link>, and requests are passed to the hotel.
      </p>
    ),
  },
  {
    id: "incidents",
    h2: "Personal data incidents",
    body: p(
      "Clefs tells the hotel without undue delay after becoming aware of a personal data breach affecting its data, with what is known at the time, and follows up as the investigation continues, so the hotel can meet its own notification duties.",
    ),
  },
  {
    id: "end",
    h2: "At the end of the contract",
    body: p(
      "The hotel can export all of its data at any time from the console. At the end of the contract Clefs returns or deletes the hotel's personal data, as the hotel chooses, except where the law requires it to be kept, and confirms the deletion in writing.",
    ),
  },
  {
    id: "signed",
    h2: "Getting the signed agreement",
    body: (
      <p>
        The signed DPA is sent with every contract and pilot agreement. To review it earlier,{" "}
        <a href={BRAND.demoUrl} target="_blank" rel="noopener noreferrer">book a call</a> and we will send it
        ahead of the meeting.
      </p>
    ),
  },
];

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <DocPage
        eyebrow="Data processing agreement"
        title="Data processing agreement"
        intro={<p>{DESCRIPTION} This page summarises the commitments in the signed agreement.</p>}
        updated={UPDATED}
        parent={{ href: "/security", label: "Security" }}
        sections={SECTIONS}
      />
    </>
  );
}
