"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Profit Guard console frame (02 spec 3.10): the page header, route-driven
   section tabs (every section has a shareable URL and back works) and the
   pg- stylesheet, injected once here for every Profit Guard page.

   Access: the nav tab gates on profit.view (owner, gm, finance). Writes
   need profit.manage; each page hides its buttons without it and the
   server stays the authority (a 403 still shows). The print route renders
   without the tabs so the preview reads like the paper it becomes.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { PageHeader, Tabs } from "../../../../ui";
import { pgHref } from "./lib";
import { pgStyles } from "./styles";

const SECTIONS: readonly { key: string; label: string; sub: string }[] = [
  { key: "overview", label: "Overview", sub: "" },
  { key: "findings", label: "Findings", sub: "/findings" },
  { key: "disputes", label: "Disputes", sub: "/disputes" },
  { key: "imports", label: "Imports", sub: "/imports" },
  { key: "channels", label: "Channels", sub: "/channels" },
  { key: "vcc", label: "Virtual cards", sub: "/vcc" },
  { key: "tax", label: "Tax", sub: "/tax" },
  { key: "contracts", label: "Contracts", sub: "/contracts" },
  { key: "parity", label: "Parity", sub: "/parity" },
];

export default function ProfitLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const pathname = usePathname() ?? "";
  const rest = pathname.split("/profit")[1] ?? "";
  const active = SECTIONS.find(s => s.sub && rest.startsWith(s.sub))?.key ?? "overview";
  const printing = /\/disputes\/[^/]+\/print\/?$/.test(rest);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: pgStyles }} />
      {!printing && (
        <>
          <PageHeader
            eyebrow="Profit Guard"
            title="OTA leakage and recoveries"
            subtitle="Every overcharge in your OTA statements, the deadline to claim it, and the credit that proves it came back."
          />
          <Tabs
            ariaLabel="Profit Guard sections"
            value={active}
            tabs={SECTIONS.map(s => ({ key: s.key, label: s.label, href: pgHref(slug, s.sub) }))}
          />
        </>
      )}
      <div className="cx-stack" style={{ marginTop: printing ? 0 : 18 }}>{children}</div>
    </>
  );
}
