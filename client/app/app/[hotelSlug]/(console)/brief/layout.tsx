"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Owner Brief console frame: page header, route-driven section tabs (each
   section has a shareable URL, and the links the brief itself sends, such
   as /brief/actions/{id} and /brief/reports/weekly/{week}, land on a tab)
   and the brf- stylesheet, injected once here for every brief page.
   Settings is shown to viewers too (read-only); the server stays the
   authority on every write.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { PageHeader, Tabs } from "../../../../ui";
import { brfHref } from "./lib";
import { brfStyles } from "./styles";

const SECTIONS: readonly { key: string; label: string; sub: string }[] = [
  { key: "today", label: "Today", sub: "" },
  { key: "actions", label: "Actions", sub: "/actions" },
  { key: "reports", label: "Weekly report", sub: "/reports" },
  { key: "settings", label: "Settings", sub: "/settings" },
];

export default function BriefLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const pathname = usePathname() ?? "";
  // Everything after ".../brief"; a hotel slug never contains "/brief".
  const rest = pathname.replace(/^.*?\/brief(?=\/|$)/, "");
  const active = SECTIONS.find(s => s.sub && (rest === s.sub || rest.startsWith(s.sub + "/")))?.key ?? "today";

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: brfStyles }} />
      <PageHeader
        eyebrow="Owner brief"
        title="Decisions that move money"
        subtitle="The few items worth your attention, ranked by money, confidence and deadline. Approve, adjust or hand them on, and see what was verified."
      />
      <Tabs
        ariaLabel="Brief sections"
        value={active}
        tabs={SECTIONS.map(s => ({ key: s.key, label: s.label, href: brfHref(slug, s.sub) }))}
      />
      <div className="cx-stack" style={{ marginTop: 18 }}>{children}</div>
    </>
  );
}
