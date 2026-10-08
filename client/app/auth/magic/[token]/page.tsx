import type { Metadata } from "next";
import MagicExchange from "./MagicExchange";

/* Magic-link landing (brief links sent by WhatsApp or email).
   - noindex: a token URL must never end up in a search index.
   - no-referrer: the token is in the path, so no outbound request from this
     page may carry it in a Referer header. */
export const metadata: Metadata = {
  title: "Signing you in",
  robots: { index: false, follow: false, nocache: true },
  referrer: "no-referrer",
};

export default async function MagicLinkPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <MagicExchange token={token} />;
}
