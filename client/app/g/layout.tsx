import type { Metadata } from "next";

/* Guest journey pages (/g/[token] preferences and consent, /g/f/[token]
   feedback). Reached only from a link in one guest's WhatsApp or email:
   - noindex, nofollow, nocache: a token URL must never reach a search index
     (robots.ts also disallows /g/, owned by the marketing lane);
   - no-referrer: the token is in the path, so no outbound request (a review
     site, a font host) may carry it in a Referer header. */
export const metadata: Metadata = {
  title: "Your stay",
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "no-referrer",
};

export default function GuestJourneyLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
