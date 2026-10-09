"use client";

/* /app/[hotelSlug]: the role landing. Replaces the old page that mounted
   StaffDashboard directly (that now lives at ./service). Each role starts
   where its day starts: the guest service desk for owners, GMs and every
   floor role (and anonymous legacy access), Profit Guard for finance and
   Demand for revenue (05 spec 2.16, nav.ts landingKey). If that module is
   off or not permitted, fall through to Service, else to the first entry
   the viewer can actually open. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useHotel } from "../../../lib/hotel";
import { useSession } from "../../../lib/session";
import { Skeleton } from "../../../ui";
import { landingKey, NAV, navHref } from "./nav";
import { landingTarget, visibleNav } from "./navVisibility";

export default function ConsoleLanding() {
  const router = useRouter();
  const session = useSession();
  const hotel = useHotel();

  const ready = Boolean(session.me) && (Boolean(hotel.context) || Boolean(hotel.error));

  useEffect(() => {
    if (!ready) return;
    const visible = visibleNav(NAV, {
      can: perm => session.can(perm),
      moduleEnabled: m => hotel.moduleEnabled(m),
      legacyMode: session.legacyMode && !session.user,
      contextReady: true,
    });
    const want = landingKey(session.role, {
      isAdmin: session.isAdmin,
      legacyAnonymous: session.legacyMode && !session.user,
    });
    const target = landingTarget(visible, want) ?? NAV.find(n => n.key === "service")!;
    router.replace(navHref(hotel.slug, target));
  }, [ready, session, hotel, router]);

  return (
    <div className="cx-stack" aria-busy="true" aria-label="Opening your console">
      <Skeleton width="36%" height={30} />
      <Skeleton lines={2} />
    </div>
  );
}
