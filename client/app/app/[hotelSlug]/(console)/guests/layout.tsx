"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Guests console frame: page header, route-driven section tabs (every
   section has a shareable URL and Back works) and the gst- stylesheet,
   injected once here for every guests and journeys page.
   Tabs the viewer holds no permission for are hidden; the server stays the
   authority (a 403 still renders), the client only hides what would fail.
   The active tab always shows, so a deep link never lands tab-less.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { PageHeader, Tabs } from "../../../../ui";
import { gstHref, PERM } from "./lib";
import { gstStyles } from "./styles";

const SECTIONS: readonly { key: string; label: string; sub: string; perm: readonly string[] }[] = [
  { key: "overview", label: "Overview", sub: "", perm: [PERM.view] },
  { key: "roster", label: "In-house", sub: "/roster", perm: [PERM.view] },
  { key: "arrivals", label: "Returning arrivals", sub: "/arrivals", perm: [PERM.view] },
  { key: "profiles", label: "Profiles", sub: "/profiles", perm: [PERM.view] },
  { key: "consent", label: "Consent", sub: "/consent", perm: [PERM.view, PERM.capture] },
  { key: "rebooking", label: "Rebooking", sub: "/rebooking", perm: [PERM.view] },
  { key: "journeys", label: "Journeys", sub: "/journeys", perm: [PERM.jView] },
];

export default function GuestsLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const pathname = usePathname() ?? "";
  // Everything after ".../guests"; the slug itself can never contain "/guests".
  const rest = pathname.replace(/^.*?\/guests(?=\/|$)/, "");
  const active = SECTIONS.find(s => s.sub && (rest === s.sub || rest.startsWith(s.sub + "/")))?.key ?? "overview";
  const visible = SECTIONS.filter(s => can(s.perm) || s.key === active);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: gstStyles }} />
      <PageHeader
        eyebrow="Guests"
        title="Guests and journeys"
        subtitle="Who is in house, who is coming back, what each guest agreed to, and the messages that go out before, during and after a stay. Guest details are masked for your role."
      />
      <Tabs
        ariaLabel="Guests sections"
        value={active}
        tabs={visible.map(s => ({ key: s.key, label: s.label, href: gstHref(slug, s.sub) }))}
      />
      <div className="cx-stack" style={{ marginTop: 18 }}>{children}</div>
    </>
  );
}
