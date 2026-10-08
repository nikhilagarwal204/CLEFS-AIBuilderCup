import type { Metadata } from "next";
import Link from "next/link";
import DocPage, { type DocSection } from "../security/_shell/DocPage";
import DemandPreviewForm from "./DemandPreviewForm";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/demand-preview";
const TITLE = "See the Next 30 Days of Demand Around Your Hotel";
const DESCRIPTION =
  "Tell us your hotel and we will send a 30-day view of the events, holidays and competitor price moves around it, with the dates that deserve a rate decision.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "website" },
};

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Demand Radar", path: "/demand-radar" }, { name: "Demand preview", path: PATH }]),
]);

// ─── Sections ────────────────────────────────────────────────────────────────

const SECTIONS: DocSection[] = [
  {
    id: "what",
    h2: "What the preview shows",
    body: (
      <ul>
        <li><strong>Events and holidays</strong> near your hotel for the next 30 days, with their venues and sources.</li>
        <li><strong>Competitor prices</strong> for a few comparable hotels on Google Hotels, labelled indicative.</li>
        <li><strong>Dates to watch</strong>: where demand and competitor moves suggest a rate decision is due.</li>
      </ul>
    ),
  },
  {
    id: "next",
    h2: "What happens next",
    body: (
      <>
        <p>
          A person at Clefs prepares the preview from Demand Radar and sends it on the channel you choose.
          There is no obligation, and we do not add you to a newsletter.
        </p>
        <p>
          If you want to see the full product running on your own data, the <Link href="/pilot">14-day
          pilot</Link> covers Demand Radar together with Profit Guard.
        </p>
      </>
    ),
  },
  {
    id: "data",
    h2: "Your details",
    body: (
      <p>
        We use these details only to prepare and send the preview and to follow up about it, and keep them
        no longer than needed for that. See <Link href="/security">security</Link> and
        the <Link href="/subprocessors">sub-processors</Link> that may process them.
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
        eyebrow="Demand preview"
        title="See the next 30 days of demand around your hotel."
        intro={<p>{DESCRIPTION}</p>}
        parent={{ href: "/demand-radar", label: "Demand Radar" }}
        sections={SECTIONS}
        toc={false}
      >
        <DemandPreviewForm />
      </DocPage>
    </>
  );
}
