"use client";

/* /app/[hotelSlug]: the role landing. Replaces the old page that mounted
   StaffDashboard directly (that now lives at ./service). Each role starts
   where its day starts: owners and GMs on the Brief, finance on Profit
   Guard, revenue on Demand, and floor roles (and anonymous legacy access)
   on Service. If that module is off or not permitted, fall through to the
   first entry the viewer can actually open. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useHotel } from "../../../lib/hotel";
import { useSession } from "../../../lib/session";
import { Skeleton } from "../../../ui";
import { landingKey, NAV, navHref } from "./nav";

export default function ConsoleLanding() {
  const router = useRouter();
  const session = useSession();
  const hotel = useHotel();

  const ready = Boolean(session.me) && (Boolean(hotel.context) || Boolean(hotel.error));

  useEffect(() => {
    if (!ready) return;
    const visible = NAV.filter(n => session.can(n.perm) && hotel.moduleEnabled(n.module));
    const want = landingKey(session.role, {
      isAdmin: session.isAdmin,
      legacyAnonymous: session.legacyMode && !session.user,
    });
    const target = visible.find(n => n.key === want) ?? visible[0] ?? NAV.find(n => n.key === "service")!;
    router.replace(navHref(hotel.slug, target));
  }, [ready, session, hotel, router]);

  return (
    <div className="cx-stack" aria-busy="true" aria-label="Opening your console">
      <Skeleton width="36%" height={30} />
      <Skeleton lines={2} />
    </div>
  );
}
