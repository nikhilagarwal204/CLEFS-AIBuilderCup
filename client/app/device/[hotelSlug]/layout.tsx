import type { Metadata } from "next";

/* Per-hotel device shell. The parent /device layout already sets noindex,
   the manifest and the viewport; this repeats noindex so the rule holds
   even if the parent layout is ever moved. */

export const metadata: Metadata = {
  title: "Floor device",
  robots: { index: false, follow: false, nocache: true },
  manifest: "/device.webmanifest",
};

export default function DeviceHotelLayout({ children }: { children: React.ReactNode }) {
  return children;
}
