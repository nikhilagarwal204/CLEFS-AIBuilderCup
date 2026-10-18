"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Microsoft Clarity, on marketing pages only (finding
   spec-clarity-on-guest-pages).

   Clarity records sessions (clicks, scrolls, typed text shapes). That is
   fine on the public marketing site and not acceptable on a guest's room
   page (/stay), the staff console (/app), a staff device (/device) or the
   login flows: those carry room numbers, guest requests and staff data.

   This component is mounted in the root layout, so it decides per path.
   An allowlist, not a blocklist: a new route stays unrecorded until
   someone adds it here on purpose. If a visitor navigates client-side
   from a marketing page into a private one, recording is stopped.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import Clarity from "@microsoft/clarity";

const PROJECT_ID = "wgkufqhun6";

/** Exact marketing paths (and their sub-pages) that may be recorded. */
const MARKETING_PATHS = [
  "/",
  "/ai-concierge-software",
  "/guest-request-management",
  "/hotel-front-desk-automation",
  "/multilingual-guest-messaging",
  "/profit-guard",
  "/demand-radar",
  "/service-control",
  "/demand-preview",
  "/pilot",
  "/security",
  "/google-check",
];

/** Sub-pages of an allowed path that carry one visitor's data: never recorded. */
const PRIVATE_SUBPATHS = ["/google-check/r"];

const under = (p: string, m: string) => p === m || p.startsWith(`${m}/`);

export function isMarketingPath(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  const p = pathname.replace(/\/+$/, "") || "/";
  if (PRIVATE_SUBPATHS.some((m) => under(p, m))) return false;
  return MARKETING_PATHS.some((m) => (m === "/" ? p === "/" : under(p, m)));
}

type ClarityQueue = (...args: unknown[]) => void;

export default function ClarityTracker() {
  const pathname = usePathname();
  const started = useRef(false);

  useEffect(() => {
    const clarity = (window as unknown as { clarity?: ClarityQueue }).clarity;
    if (isMarketingPath(pathname)) {
      if (!started.current) {
        Clarity.init(PROJECT_ID);
        started.current = true;
      } else {
        // Back on a marketing page after a private one: resume as before.
        Clarity.consent(true);
        clarity?.("start");
      }
      return;
    }
    // A private page: stop a recording a marketing page started, and drop
    // Clarity's cookies. Nothing is initialised on a direct visit.
    if (started.current) {
      clarity?.("stop");
      Clarity.consent(false);
    }
  }, [pathname]);

  return null;
}
