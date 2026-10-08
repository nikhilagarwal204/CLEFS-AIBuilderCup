import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "./_shell/DocPage";
import DataFlow from "./_components/DataFlow";
import { AccessTable, RetentionTable, SubprocessorTable } from "./_components/Tables";
import { RESIDENCY } from "../lib/subprocessors";
import { PURGE_SCHEDULE } from "../lib/retention";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/security";
const TITLE = "Security and Data Protection";
const DESCRIPTION =
  "Where Clefs stores hotel and guest data, which providers process it, how long it is kept, who at the hotel can see what, and how every change is recorded.";
const UPDATED = "2026-09-29";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "article" },
};

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Security", path: PATH }]),
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

// HONESTY RULE (03 section 5.3): state where data is today. A pen-test line is
// deliberately absent: it is added only when a real report exists.
const RESIDENCY_SECTION: DocSection = {
  id: "residency",
  h2: "Where your data lives",
  body: (
    <>
      <p>{RESIDENCY.statement}</p>
      <ul>
        <li><strong>Database:</strong> {RESIDENCY.database}.</li>
        <li><strong>AI processing:</strong> {RESIDENCY.ai}.</li>
        <li><strong>UAE hosting:</strong> {RESIDENCY.uaeOption}.</li>
      </ul>
      <div className="mk-note">
        <p>
          Moving the database to the UAE does not remove the transfer abroad: AI processing still runs in
          the United States. That is why guests are asked to consent to it, and why the assistant does not
          start without that consent.
        </p>
      </div>
      <p>
        Clefs follows the UAE Personal Data Protection Law with consent as the basis for processing guest
        data. We review this page with counsel as the executive regulations are issued.
      </p>
    </>
  ),
};

const FLOW_SECTION: DocSection = {
  id: "data-flow",
  h2: "How data moves",
  body: <DataFlow />,
};

const CONSENT_SECTION: DocSection = {
  id: "consent",
  h2: "Guest consent",
  body: (
    <>
      <p>
        Consent is the only basis Clefs uses for guest data; we never rely on legitimate interest. Each
        consent is stored separately, with the text version and language the guest saw, and can be
        withdrawn at any time.
      </p>
      <ul>
        <li><strong>AI processing</strong>: the assistant answers the guest using AI.</li>
        <li><strong>Transfer abroad</strong>: the guest&apos;s messages are processed outside the UAE.</li>
        <li><strong>Recording</strong>: phone calls are recorded only if the hotel switches it on and the guest agrees.</li>
        <li><strong>Profile memory</strong>: preferences are kept for a later stay only with the guest&apos;s agreement.</li>
        <li><strong>Marketing</strong>: offers are sent only on the channel the guest agreed to, inside UAE marketing hours.</li>
      </ul>
    </>
  ),
};

const PROCESSORS_SECTION: DocSection = {
  id: "subprocessors",
  h2: "Sub-processors",
  body: (
    <>
      <p>
        These providers process data on our behalf. The full list, with what each one receives, is also on
        the <Link href="/subprocessors">sub-processors page</Link>, which we update before a new provider
        starts.
      </p>
      <SubprocessorTable compact />
    </>
  ),
};

const RETENTION_SECTION: DocSection = {
  id: "retention",
  h2: "How long data is kept",
  body: (
    <>
      <p>
        These are the defaults every hotel starts with. The hotel&apos;s owner can shorten most of them,
        within the legal minimums. {PURGE_SCHEDULE}, expired data is deleted or anonymized in batches, and
        the deletion log records how many rows were removed, never what they contained.
      </p>
      <RetentionTable />
    </>
  ),
};

const ACCESS_SECTION: DocSection = {
  id: "access",
  h2: "Who at the hotel can see what",
  body: (
    <>
      <p>
        Every person signs in with their own account and a role: owner, GM, finance, revenue, department
        head, front desk, or a shared floor device where each staff member signs in to a shift with a PIN.
        Guest details are masked by role on every screen and every export, and an unknown role sees
        nothing.
      </p>
      <AccessTable />
      <p>
        Actions that move money (refunds, rate changes, OTA disputes) always need an approval from a
        person with the right role, whatever the automation settings.
      </p>
    </>
  ),
};

const AUDIT_SECTION: DocSection = {
  id: "audit",
  h2: "Audit log",
  body: (
    <>
      <p>
        Every change is written to an append-only audit log in the same transaction as the change itself:
        who did it (a person, a staff device, the AI assistant or a system job), what changed, and why.
        Personal details in the log are reduced to field names and fingerprints.
      </p>
      <ul>
        <li>The database refuses edits and deletions of log entries.</li>
        <li>Entries are chained by hash per hotel, and the chain is verified every night.</li>
        <li>AI actions record the model and prompt version; a person correcting the AI is marked as an override.</li>
        <li>The owner and GM can search the log and export it from the console.</li>
      </ul>
    </>
  ),
};

const RIGHTS_SECTION: DocSection = {
  id: "rights",
  h2: "Guest rights and your data",
  body: (
    <>
      <p>
        Guests can ask to see, correct, erase, restrict or move their data, or object to its use, through
        the <Link href="/privacy/request">privacy request form</Link>. Requests reach the hotel&apos;s
        console with a due date, and a reminder runs daily until each one is closed.
      </p>
      <p>
        The hotel owns its data. The owner can export all of it at any time, and at the end of a contract
        we export or delete it as the hotel chooses. Our terms as a processor are in
        the <Link href="/dpa">data processing agreement</Link>.
      </p>
    </>
  ),
};

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <DocPage
        eyebrow="Security"
        title="Security and data protection"
        intro={<p>{DESCRIPTION}</p>}
        updated={UPDATED}
        sections={[
          RESIDENCY_SECTION, FLOW_SECTION, CONSENT_SECTION, PROCESSORS_SECTION,
          RETENTION_SECTION, ACCESS_SECTION, AUDIT_SECTION, RIGHTS_SECTION,
        ]}
      />
    </>
  );
}
