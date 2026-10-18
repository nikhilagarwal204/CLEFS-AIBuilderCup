"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Journeys sub-navigation (overview, templates, offers, settings). A
   reservation timeline is reached from a row elsewhere, so it has no tab;
   it highlights Overview. Hidden tabs follow the same rule as the guests
   frame: the server decides, the client hides only what would 403.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Segmented } from "../../../../../ui";
import { gstHref, PERM } from "../lib";

const SUBS = [
  { key: "overview", label: "Overview", sub: "" },
  { key: "templates", label: "Templates", sub: "/templates" },
  { key: "offers", label: "Offers", sub: "/offers" },
  { key: "settings", label: "Settings", sub: "/settings" },
] as const;

type SubKey = (typeof SUBS)[number]["key"];

export default function JourneysLayout({ children }: { children: ReactNode }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const rest = pathname.replace(/^.*?\/guests\/journeys(?=\/|$)/, "");
  const active: SubKey = SUBS.find(s => s.sub && (rest === s.sub || rest.startsWith(s.sub + "/")))?.key ?? "overview";

  if (!can(PERM.jView)) {
    return <div className="gst-muted">You need the guests.journeys.view permission to see journeys.</div>;
  }

  return (
    <>
      <div className="cx-row" style={{ justifyContent: "space-between" }}>
        <Segmented<SubKey>
          ariaLabel="Journey sections"
          size="sm"
          value={active}
          onChange={k => router.push(gstHref(slug, `/journeys${SUBS.find(s => s.key === k)?.sub ?? ""}`))}
          options={SUBS.map(s => ({ value: s.key, label: s.label }))}
        />
        <span className="gst-small gst-muted">Nothing sends until you approve its template and turn its step up on the dial.</span>
      </div>
      {children}
    </>
  );
}
