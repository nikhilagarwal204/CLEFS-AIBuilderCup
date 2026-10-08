"use client";

/* ═══════════════════════════════════════════════════════════════════════
   In-room phone AI (03 spec 3.6), under Concierge because it is the same
   concierge on a different surface: same knowledge, same tools, same
   request queue. Three sub-sections with their own URLs:
     - Lines: the SIP line(s), their secrets and the extension-to-room map;
     - Setup guide: PBX recipes, regulatory notes, the live webhook panel
       and the test-call checklist;
     - Calls: the call log with transcripts, masked by role.
   Hidden entirely when the hotel does not have the voice module.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { LuPhone } from "react-icons/lu";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Card, EmptyState, Tabs } from "../../../../../ui";
import { PERM, ccHref } from "../lib";

const SUBS = [
  { key: "lines", label: "Lines and extensions", sub: "" },
  { key: "setup", label: "Setup guide", sub: "/setup" },
  { key: "calls", label: "Call log", sub: "/calls" },
] as const;

export default function VoiceLayout({ children }: { children: ReactNode }) {
  const { slug, moduleEnabled, context } = useHotel();
  const { can } = useSession();
  const pathname = usePathname() ?? "";
  const rest = pathname.split(ccHref(slug, "/voice"))[1] ?? pathname.split(`/${slug}/concierge/voice`)[1] ?? "";
  const active = SUBS.find(s => s.sub && rest.startsWith(s.sub))?.key ?? "lines";

  if (context && !moduleEnabled("voice")) {
    return <Card><EmptyState icon={<LuPhone />} title="In-room phone is not switched on"
      body="Ask Clefs to enable the phone module for this hotel. Guests then dial an extension and talk to the same concierge." /></Card>;
  }
  if (!can(PERM.voiceView)) {
    return <Card><EmptyState icon={<LuPhone />} title="Not available for your role" body="The owner, GM and front desk can see the phone line." /></Card>;
  }
  return (
    <>
      <Tabs ariaLabel="Phone sections" value={active}
        tabs={SUBS.map(s => ({ key: s.key, label: s.label, href: ccHref(slug, `/voice${s.sub}`) }))} />
      <div className="cx-stack">{children}</div>
    </>
  );
}
