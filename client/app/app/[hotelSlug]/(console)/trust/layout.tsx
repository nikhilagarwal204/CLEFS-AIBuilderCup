"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Trust console frame: the page header, route-driven section tabs (each
   section has a shareable URL and back works) and the trs- stylesheet,
   injected once here for every Trust page.
   Tabs are hidden when the viewer holds none of their permissions; the
   server stays the authority (a 403 still shows), the client only hides
   what would fail. The active tab always shows, so a deep link never lands
   on a page whose tab is missing.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { PageHeader, Tabs } from "../../../../ui";
import { trustHref } from "./lib";
import { trsStyles } from "./styles";

const SECTIONS: readonly { key: string; label: string; sub: string; perm: readonly string[] }[] = [
  { key: "overview", label: "Overview", sub: "", perm: ["trust.view"] },
  { key: "audit", label: "Audit log", sub: "/audit", perm: ["trust.audit.view"] },
  { key: "evals", label: "Quality gate", sub: "/evals", perm: ["trust.evals.view", "concierge.manage"] },
  { key: "privacy", label: "Privacy", sub: "/privacy", perm: ["trust.privacy.manage", "trust.view"] },
  { key: "pilots", label: "Pilots", sub: "/pilots", perm: ["trust.pilots.view"] },
  { key: "benchmarks", label: "Benchmarks", sub: "/benchmarks", perm: ["trust.pilots.view"] },
];

export default function TrustLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const pathname = usePathname() ?? "";
  const rest = pathname.split("/trust")[1] ?? "";
  // The metric viewer belongs to the pilot report it is opened from.
  const active = rest.startsWith("/metric")
    ? "pilots"
    : SECTIONS.find(s => s.sub && rest.startsWith(s.sub))?.key ?? "overview";
  const visible = SECTIONS.filter(s => can(s.perm) || s.key === active);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: trsStyles }} />
      <PageHeader
        eyebrow="Trust"
        title="Quality gate and trust"
        subtitle="The concierge talks to guests only after all 22 safety and accuracy tests pass, judged by Gemini, and 7 days in shadow mode. Here too: what changed and who changed it, privacy requests, and pilot results you can check line by line."
      />
      <Tabs
        ariaLabel="Trust sections"
        value={active}
        tabs={visible.map(s => ({ key: s.key, label: s.label, href: trustHref(slug, s.sub) }))}
      />
      <div className="cx-stack" style={{ marginTop: 18 }}>{children}</div>
    </>
  );
}
