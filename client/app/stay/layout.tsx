import type { Metadata } from "next";

// The guest concierge is a private, per-room surface reached only by the QR code
// in that room. It must never appear in search results, and robots.ts disallows
// crawling it; this noindex is the belt-and-braces signal for URLs discovered
// some other way (a shared link, a referrer, a pasted QR target).
//
// The title is the guest's, not the marketing site's (QA GS-10): a guest who
// switches tabs sees "Concierge", and the page swaps in the hotel's name once
// its context loads (DocumentMeta). `absolute` skips the "| Clefs" template.
export const metadata: Metadata = {
  title: { absolute: "Concierge" },
  description: "Your in-room concierge.",
  robots: { index: false, follow: false, nocache: true },
};

export default function StayLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
