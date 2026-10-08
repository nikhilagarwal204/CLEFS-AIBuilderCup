import type { Metadata } from "next";

/* Embeddable widgets (/w/inquiry/[slug]). They live inside an iframe on a
   hotel's own website:
   - noindex, nofollow: the form belongs to the hotel's page, not to a
     search result of its own (robots.ts also disallows /w/);
   - strict-origin-when-cross-origin: API calls stay same-origin, and the
     widget key in the query string never leaves in a Referer. */
export const metadata: Metadata = {
  title: "Inquiry",
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "strict-origin-when-cross-origin",
};

export default function WidgetLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
