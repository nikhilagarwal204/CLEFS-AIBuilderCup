import type { Metadata } from "next";

// Staff dashboard: internal, per-property, and behind hotel-specific URLs.
// Never indexable.
export const metadata: Metadata = {
  robots: { index: false, follow: false, nocache: true },
};

export default function StaffAreaLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
