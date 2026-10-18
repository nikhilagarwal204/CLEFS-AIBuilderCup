import type { Metadata } from "next";
import type { ReactNode } from "react";

/* The owner's mobile brief (/brief/[token]), reached only from the brief's
   own WhatsApp "Open brief" button or email link:
   - noindex, nofollow, nocache: a token URL must never reach a search index
     (robots.ts also disallows /brief/, and the API sends X-Robots-Tag);
   - no-referrer: the token is in the path, so no outbound request from this
     page (an evidence link, the console) may carry it in a Referer header. */
export const metadata: Metadata = {
  title: "Your brief",
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "no-referrer",
};

export default function BriefLinkLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
