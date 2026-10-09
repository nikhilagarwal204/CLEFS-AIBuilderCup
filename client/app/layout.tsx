import type { Metadata, Viewport } from "next";
import "./globals.css";
import ClarityTracker from "./ClarityTracker";
import { SpeedInsights } from "@vercel/speed-insights/next";

// clefs.world 308-redirects to www.clefs.world, so www is the canonical host.
// metadataBase MUST stay set: without it Next falls back to VERCEL_URL and every
// og:image/canonical resolves against the preview domain (clefs-ecru.vercel.app),
// which breaks social cards and leaks the preview host to crawlers.
const SITE = "https://www.clefs.world";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: {
    default: "Clefs - Profit and Service Control for Hotels",
    template: "%s | Clefs",
  },
  description:
    "Clefs finds the profit leaks your hotel systems miss. It audits OTA payouts and rate integrity, spots local demand early, and sends the owner a morning brief of actions to approve. For hotels in the UAE.",
  applicationName: "Clefs",
  authors: [{ name: "Clefs" }],
  creator: "Clefs",
  publisher: "Clefs",
  category: "Hospitality Technology",
  alternates: { canonical: "/" },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  openGraph: {
    title: "Clefs - Find the profit leaks your hotel systems miss",
    description:
      "OTA payout and rate integrity audits, a local demand radar and a morning owner brief of evidenced actions to approve. For hotels in the UAE.",
    url: SITE,
    siteName: "Clefs",
    locale: "en_US",
    // og:image and twitter:image come from app/opengraph-image.jpg via Next's
    // file convention - it emits the absolute URL and the real dimensions.
    // Repeating them here would put a second, duplicate image on every card.
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Clefs - Find the profit leaks your hotel systems miss",
    description:
      "OTA payout and rate integrity audits, a local demand radar and a morning owner brief of evidenced actions to approve. For hotels in the UAE.",
  },
};

/* Declared through Next's `viewport` export rather than a hand-written <meta>
   in <head>. With a manual tag Next still emitted its own default viewport, so
   the page shipped TWO viewport metas - browsers honour the first, which meant
   viewport-fit=cover survived by luck of ordering rather than by intent.

   user-scalable=no / maximum-scale=1 stay OFF: blocking pinch-zoom is a WCAG
   1.4.4 failure and Lighthouse flags it under Accessibility, which feeds the
   page-experience signals. viewport-fit is kept for the iOS safe-area insets
   the guest concierge relies on. */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#070C14",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="mobile-web-app-capable" content="yes" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body style={{ margin: 0, padding: 0 }} suppressHydrationWarning>
        <ClarityTracker />
        {children}
        {/* Vercel serves the Speed Insights script; elsewhere (Cloud Run) it would 404 on every page. */}
        {process.env.VERCEL ? <SpeedInsights /> : null}
      </body>
    </html>
  );
}
