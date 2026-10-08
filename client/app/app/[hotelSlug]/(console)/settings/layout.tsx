"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { PageHeader, Tabs } from "../../../../ui";

/* `perm` hides a tab the viewer could only get a 403 from. Autonomy has its
   own view permission (core.autonomy.view, CONTRACTS.md Part 2). */
const SECTIONS: readonly { key: string; label: string; sub: string; perm?: string }[] = [
  { key: "profile", label: "Hotel profile", sub: "" },
  { key: "team", label: "Team & roles", sub: "/team" },
  { key: "devices", label: "Devices", sub: "/devices" },
  { key: "rooms", label: "Rooms", sub: "/rooms" },
  { key: "imports", label: "Imports", sub: "/imports" },
  { key: "tax", label: "Tax", sub: "/tax", perm: "profit.view" },
  { key: "modules", label: "Modules", sub: "/modules" },
  { key: "autonomy", label: "Autonomy", sub: "/autonomy", perm: "core.autonomy.view" },
];

/* Settings sub-navigation. Route-driven tabs (not local state) so each
   section has a shareable URL and the browser back button works. */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const pathname = usePathname() ?? "";
  const base = `/app/${encodeURIComponent(slug)}/settings`;
  const rest = pathname.split("/settings")[1] ?? "";
  const active = SECTIONS.find(s => s.sub && rest.startsWith(s.sub))?.key ?? "profile";
  // The active tab always shows, so a deep link never lands on a page whose tab is missing.
  const visible = SECTIONS.filter(s => !s.perm || can(s.perm) || s.key === active);

  return (
    <>
      <PageHeader eyebrow="Settings" title="Hotel settings"
                  subtitle="The profile every module reads, who can see what, the devices on your floors and how much Clefs does on its own." />
      <Tabs
        ariaLabel="Settings sections"
        value={active}
        tabs={visible.map(s => ({ key: s.key, label: s.label, href: base + s.sub }))}
      />
      {children}
    </>
  );
}
