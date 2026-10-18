import type { Metadata, Viewport } from "next";

/* The floor-device PWA (03 spec 1.8, Canon 13: top-level /device/ for PWA
   scope). Private per-property surface: never indexed, never followed, and
   no analytics (ClarityTracker records marketing paths only, an allowlist).
   The manifest scopes the installed app to /device/. */

export const metadata: Metadata = {
  title: { default: "Floor device", template: "%s | Clefs floor" },
  description: "Clefs floor device: service requests, alerts and handover.",
  manifest: "/device.webmanifest",
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  alternates: { canonical: null },
  openGraph: null,
  appleWebApp: { capable: true, title: "Clefs Floor", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#070C14",
};

export default function DeviceLayout({ children }: { children: React.ReactNode }) {
  return children;
}
