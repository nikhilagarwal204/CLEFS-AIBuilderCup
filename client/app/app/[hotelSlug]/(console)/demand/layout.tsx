"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Demand Radar console frame: page header, route-driven section tabs (each
   section has a shareable URL and Back works) and the dr- stylesheet,
   injected once here for every demand page. Settings is hidden from roles
   without demand.manage; the server stays the authority and the active tab
   always shows, so a deep link never lands tab-less.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { EmptyState, PageHeader, Tabs } from "../../../../ui";
import { drHref, PERM } from "./_lib/paths";
import { drStyles } from "./_lib/styles";

const SECTIONS: readonly { key: string; label: string; sub: string; perm: string }[] = [
  { key: "calendar", label: "Calendar", sub: "", perm: PERM.view },
  { key: "events", label: "Events", sub: "/events", perm: PERM.view },
  { key: "top5", label: "Top 5", sub: "/top5", perm: PERM.view },
  { key: "outcomes", label: "Outcomes", sub: "/outcomes", perm: PERM.view },
  { key: "campaigns", label: "Campaigns", sub: "/campaigns", perm: PERM.view },
  { key: "settings", label: "Settings", sub: "/settings", perm: PERM.manage },
];

export default function DemandLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const { can, loading } = useSession();
  const pathname = usePathname() ?? "";
  // Everything after ".../demand"; a slug can never contain "/demand".
  const rest = pathname.replace(/^.*?\/demand(?=\/|$)/, "");
  const active = SECTIONS.find(s => s.sub && (rest === s.sub || rest.startsWith(s.sub + "/")))?.key ?? "calendar";
  const visible = SECTIONS.filter(s => can(s.perm) || s.key === active);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: drStyles }} />
      <PageHeader
        eyebrow="Demand Radar"
        title="Demand and pricing"
        subtitle="Events near the hotel, indicative competitor rates and your pace for the next 90 nights, with rate suggestions kept inside your floor and ceiling. Nothing is sent to your channel manager: you decide, then apply."
      />
      <Tabs
        ariaLabel="Demand sections"
        value={active}
        tabs={visible.map(s => ({ key: s.key, label: s.label, href: drHref(slug, s.sub) }))}
      />
      <div className="cx-stack" style={{ marginTop: 18 }}>
        {!loading && !can(PERM.view)
          ? <EmptyState title="No access to Demand Radar" body="Ask the owner or GM for the demand.view permission." />
          : children}
      </div>
    </>
  );
}
