import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "../security/_shell/DocPage";
import { SubprocessorTable } from "../security/_components/Tables";
import { RESIDENCY, SUBPROCESSORS } from "../lib/subprocessors";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/subprocessors";
const TITLE = "Sub-processors";
const DESCRIPTION =
  "Every provider that processes data for Clefs, what it receives, and where the processing happens.";
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

// Grouped by what a hotel reader cares about: does guest data go there?
const withPersonal = SUBPROCESSORS.filter((s) => !s.data.includes("none"));
const withoutPersonal = SUBPROCESSORS.filter((s) => s.data.includes("none"));

// ─── Sections ────────────────────────────────────────────────────────────────

const SECTIONS: DocSection[] = [
  {
    id: "personal",
    h2: "Providers that receive personal data",
    body: (
      <>
        <p>
          These providers receive personal data of guests, hotel staff, enquirers or website visitors, only
          for the purpose shown. Each is bound by a data processing agreement with Clefs.
        </p>
        <SubprocessorTable rows={withPersonal} />
      </>
    ),
  },
  {
    id: "no-personal",
    h2: "Providers that receive no personal data",
    body: (
      <>
        <p>
          These services receive hotel names, dates and public questions only. No guest or staff details
          are sent to them.
        </p>
        <SubprocessorTable rows={withoutPersonal} />
      </>
    ),
  },
  {
    id: "transfers",
    h2: "Transfers outside the UAE",
    body: (
      <>
        <p>{RESIDENCY.statement}</p>
        <p>
          The <Link href="/security#data-flow">data-flow diagram</Link> shows which flows leave the UAE.
        </p>
      </>
    ),
  },
  {
    id: "changes",
    h2: "Changes to this list",
    body: (
      <p>
        We update this page before a new provider starts processing personal data, and we tell hotels
        with an active contract by email. A hotel may object to a new provider under the terms of
        the <Link href="/dpa">data processing agreement</Link>.
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
        eyebrow="Sub-processors"
        title="Sub-processors"
        intro={<p>{DESCRIPTION}</p>}
        updated={UPDATED}
        parent={{ href: "/security", label: "Security" }}
        sections={SECTIONS}
      />
    </>
  );
}
