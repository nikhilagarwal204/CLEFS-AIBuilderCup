import type { Metadata } from "next";
import { notFound } from "next/navigation";
import DocPage from "../../../security/_shell/DocPage";
import AuditResult from "./AuditResult";
import { validToken } from "../../_view";
import { OTA_AUDIT_PATH } from "../../../lib/seo";

/**
 * The tokenised report of one visitor's audit (02 spec 3.11). noindex and
 * no-referrer: the URL is the only key to a hotel's statement findings, it
 * must not reach a search index nor leak through a Referer header when the
 * visitor follows a link out (the AI snapshot cites third-party sites). The
 * report expires after 30 days; the client shows "expired" on a 404.
 */

export const metadata: Metadata = {
  title: "Your OTA audit",
  robots: { index: false, follow: false, nocache: true },
  referrer: "no-referrer",
};

type Props = { params: Promise<{ token: string }> };

export default async function Page({ params }: Props) {
  const { token } = await params;
  if (!validToken(token)) notFound();
  return (
    <DocPage
      eyebrow="Your audit"
      title="Your OTA statement audit"
      intro={<p>What the checks found in the statement you uploaded, under default assumptions for a Dubai hotel.</p>}
      parent={{ href: OTA_AUDIT_PATH, label: "Free OTA audit" }}
      sections={[]}
      toc={false}
    >
      <AuditResult token={token} />
    </DocPage>
  );
}
