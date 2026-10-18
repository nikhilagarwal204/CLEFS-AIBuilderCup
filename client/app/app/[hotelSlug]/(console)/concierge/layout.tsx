"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Concierge console frame: page header, route-driven section tabs (each
   section has its own URL, so back works and a link can be shared) and the
   cc- stylesheet, injected once here for every Concierge and Phone page.
   Tabs are hidden when the viewer lacks their permission or the hotel has
   the module off; the server stays the authority (a 403 still shows). The
   active tab always shows, so a deep link never lands on a missing tab.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { PageHeader, Tabs } from "../../../../ui";
import { PERM, ccHref } from "./lib";
import { ccStyles } from "./styles";

interface Section { key: string; label: string; sub: string; perm: readonly string[]; module?: string }

const SECTIONS: readonly Section[] = [
  { key: "overview", label: "Overview", sub: "", perm: [PERM.view] },
  { key: "knowledge", label: "Knowledge", sub: "/knowledge", perm: [PERM.view] },
  { key: "unknowns", label: "Not answered", sub: "/unknowns", perm: [PERM.view] },
  { key: "venues", label: "Venues", sub: "/venues", perm: [PERM.view] },
  { key: "transcripts", label: "Transcripts", sub: "/transcripts", perm: [PERM.view] },
  { key: "settings", label: "Settings", sub: "/settings", perm: [PERM.manage, PERM.settings] },
  { key: "prompt", label: "Prompt preview", sub: "/prompt", perm: [PERM.manage] },
  { key: "voice", label: "Phone", sub: "/voice", perm: [PERM.voiceView], module: "voice" },
];

export default function ConciergeLayout({ children }: { children: ReactNode }) {
  const { slug, moduleEnabled } = useHotel();
  const { can } = useSession();
  const pathname = usePathname() ?? "";
  // Anchored on the slug so a hotel whose slug contains "concierge" still
  // resolves its section.
  const rest = pathname.split(`${ccHref(slug)}`)[1] ?? pathname.split(`/${slug}/concierge`)[1] ?? "";
  const active = SECTIONS.find(s => s.sub && (rest === s.sub || rest.startsWith(`${s.sub}/`)))?.key ?? "overview";
  const visible = SECTIONS.filter(
    s => s.key === active || (can(s.perm) && (!s.module || moduleEnabled(s.module))),
  );

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: ccStyles }} />
      <PageHeader
        eyebrow="Concierge"
        title="AI concierge"
        subtitle="What the concierge is allowed to tell guests, what they asked that it could not answer, and the in-room phone line."
      />
      <Tabs
        ariaLabel="Concierge sections"
        value={active}
        tabs={visible.map(s => ({ key: s.key, label: s.label, href: ccHref(slug, s.sub) }))}
      />
      <div className="cx-stack" style={{ marginTop: 18 }}>{children}</div>
    </>
  );
}
