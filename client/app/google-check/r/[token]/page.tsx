import type { Metadata } from "next";
import { notFound } from "next/navigation";
import DocPage from "../../../security/_shell/DocPage";
import ResultView from "./ResultView";
import { validToken } from "../../_view";

/**
 * /google-check/r/<token>: one visitor's check result (04 section 2.4).
 * noindex and disallowed in robots.ts: it holds a named hotel's prices for
 * specific dates and expires after 30 days.
 */

export const metadata: Metadata = {
  title: "Your Google check",
  robots: { index: false, follow: false },
};

type Props = { params: Promise<{ token: string }> };

export default async function Page({ params }: Props) {
  const { token } = await params;
  if (!validToken(token)) notFound();
  return (
    <DocPage
      eyebrow="Your result"
      title="Your Google check"
      intro={<p>Whether your official site price shows on Google Hotels, and how it compares with the OTAs.</p>}
      parent={{ href: "/google-check", label: "Google check" }}
      sections={[]}
      toc={false}
    >
      <ResultView token={token} />
    </DocPage>
  );
}
