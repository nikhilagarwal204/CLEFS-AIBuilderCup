"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Finance console frame (04 spec 6.8): the page header, route-driven
   section tabs (each section has a shareable URL and back works) and the
   fin- stylesheet, injected once here for every Finance page.

   Access: the nav tab gates on finance.view, which revenue holds too.
   Revenue sees the weekly pulse only; every P&L section needs
   finance.pl.view. Tabs are hidden when the viewer lacks their permission;
   the server stays the authority (a 403 still shows). The active tab always
   shows, so a deep link never lands on a page whose tab is missing.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { PageHeader, Tabs } from "../../../../ui";
import { finHref } from "./lib";
import { finStyles } from "./styles";

const SECTIONS: readonly { key: string; label: string; sub: string; perm: readonly string[] }[] = [
  { key: "overview", label: "Overview", sub: "", perm: ["finance.view", "finance.weekly.view"] },
  { key: "imports", label: "P&L imports", sub: "/imports", perm: ["finance.pl.view"] },
  { key: "mapping", label: "Account mapping", sub: "/mapping", perm: ["finance.pl.view"] },
  { key: "trend", label: "Trends", sub: "/trend", perm: ["finance.pl.view", "finance.weekly.view"] },
  { key: "settings", label: "Settings", sub: "/settings", perm: ["finance.pl.view"] },
];

export default function FinanceLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const pathname = usePathname() ?? "";
  const rest = pathname.split("/finance")[1] ?? "";
  const active = SECTIONS.find(s => s.sub && rest.startsWith(s.sub))?.key ?? "overview";
  const visible = SECTIONS.filter(s => can(s.perm) || s.key === active);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: finStyles }} />
      <PageHeader
        eyebrow="Finance"
        title="Owner P&L pulse"
        subtitle="What Clefs verifiably added, a weekly room-revenue pulse that needs no P&L, and your monthly P&L in USALI terms once you upload it."
      />
      {visible.length > 1 && (
        <Tabs
          ariaLabel="Finance sections"
          value={active}
          tabs={visible.map(s => ({ key: s.key, label: s.label, href: finHref(slug, s.sub) }))}
        />
      )}
      <div className="cx-stack" style={{ marginTop: 18 }}>{children}</div>
    </>
  );
}
