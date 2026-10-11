import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "../security/_shell/DocPage";
import { SubprocessorTable } from "../security/_components/Tables";
import { RESIDENCY, inUseSubprocessors, optionalSubprocessors } from "../lib/subprocessors";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/subprocessors";
const TITLE = "Sub-processors";
const DESCRIPTION =
  "Every provider that processes data for Clefs, what it receives, and where the processing happens.";
const UPDATED = "2026-10-21";

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

// Grouped by what a hotel reader cares about: is it in use, and does guest
// data go there?
const inUse = inUseSubprocessors();
const withPersonal = inUse.filter((s) => !s.data.includes("none"));
const withoutPersonal = inUse.filter((s) => s.data.includes("none"));
const optional = optionalSubprocessors();

// ─── Sections ────────────────────────────────────────────────────────────────

const SECTIONS: DocSection[] = [
  {
    id: "personal",
    h2: "Providers that receive personal data",
    body: (
      <>
        <p>
          These providers are in use today and receive personal data of guests, hotel staff or website
          visitors, only for the purpose shown. Each is bound by a data processing agreement with Clefs.
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
          These services are in use but receive no guest or staff details.
        </p>
        <SubprocessorTable rows={withoutPersonal} />
      </>
    ),
  },
  {
    id: "when-enabled",
    h2: "Providers used only when a hotel turns a feature on",
    body: (
      <>
        <p>
          These providers are not used in this deployment. Each one starts only once a hotel switches on the
          feature shown, such as WhatsApp messaging or SMS, and receives nothing before that.
        </p>
        <SubprocessorTable rows={optional} optional />
        <p>
          Other modules in Clefs, such as reviews, enquiries with payment links and finance, are switched off
          here. Before a hotel turns one on, its providers are added to this page.
        </p>
      </>
    ),
  },
  {
    id: "transfers",
    h2: "Transfers abroad",
    body: (
      <>
        <p>{RESIDENCY.statement}</p>
        <p>
          The <Link href="/security#data-flow">data-flow diagram</Link> shows which flows can leave India.
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
