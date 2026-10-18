import type { Metadata } from "next";

/* Inquiry quote pages (/q/[token]). Reached only from a link in one
   guest's WhatsApp or email:
   - noindex, nofollow, nocache: a token URL must never reach a search
     index (robots.ts also disallows /q/, and the API sends X-Robots-Tag);
   - no-referrer: the token is in the path, so the payment provider the
     guest goes on to must not receive it in a Referer header. */
export const metadata: Metadata = {
  title: "Your quote",
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "no-referrer",
};

export default function QuoteLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
