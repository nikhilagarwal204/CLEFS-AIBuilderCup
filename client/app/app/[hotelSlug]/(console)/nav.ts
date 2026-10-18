/* ═══════════════════════════════════════════════════════════════════════
   Console navigation: EVERY module is pre-declared here by the foundation,
   in display order, so no lane ever edits this file (it is frozen after
   Wave 0). ConsoleShell shows an entry only when the viewer holds `perm`
   (resolved server-side, see useSession().can) AND the hotel has `module`
   enabled (GET /hotels/{slug}/context -> modules_enabled).

   `module` is the SERVER registry name (server/clefs/registry.py), which is
   why Profit Guard is "profit_guard" and the menu is "upsell" even though
   their routes are /profit and /menu. `perm` is the tab gate the server
   registers, "<first route segment>.view" (CONTRACTS.md section 6), so it is
   "profit.view" and "menu.view", with core settings under "core.". A server
   test (test_module_contracts.py) checks every perm below is registered and
   held by the owner role, so a key the server never issues fails the build.
   ═══════════════════════════════════════════════════════════════════════ */

import type { IconType } from "react-icons";
import {
  LuBadgeDollarSign, LuBot, LuConciergeBell, LuEye, LuGift, LuInbox, LuLandmark, LuMessageSquare,
  LuRadar, LuSettings, LuShieldCheck, LuStar, LuSunrise, LuUsers, LuUtensils,
} from "react-icons/lu";

export type NavKey =
  | "brief" | "service" | "concierge" | "profit" | "demand" | "inquiries" | "amenities" | "menu"
  | "reviews" | "guests" | "visibility" | "finance" | "ask" | "trust" | "settings";

export interface NavItem {
  key: NavKey;
  label: string;
  /** Route segment under /app/[hotelSlug]/. */
  href: string;
  /** Permission required to see the entry. */
  perm: string;
  /** Server module name gating the entry via modules_enabled. */
  module: string;
  icon: IconType;
  /** One line for stubs and tooltips. */
  blurb: string;
  /** The page owns its whole viewport (StaffDashboard brings its own layout and sub-nav). */
  fullBleed?: boolean;
  /** Shown in the phone bottom bar (max four); the rest live under "More". */
  mobilePrimary?: boolean;
}

export const NAV: readonly NavItem[] = [
  { key: "brief", label: "Brief", href: "brief", perm: "brief.view", module: "brief", icon: LuSunrise, mobilePrimary: true,
    blurb: "Today's money at risk and the five things worth your time." },
  { key: "service", label: "Service", href: "service", perm: "service.view", module: "service", icon: LuConciergeBell, fullBleed: true, mobilePrimary: true,
    blurb: "Live guest requests, staff and response times." },
  { key: "concierge", label: "Concierge", href: "concierge", perm: "concierge.view", module: "concierge", icon: LuBot,
    blurb: "What the AI concierge knows, and what guests asked it." },
  { key: "profit", label: "Profit Guard", href: "profit", perm: "profit.view", module: "profit_guard", icon: LuBadgeDollarSign, mobilePrimary: true,
    blurb: "OTA statement leakage, recoveries and net ADR by channel." },
  { key: "demand", label: "Demand", href: "demand", perm: "demand.view", module: "demand", icon: LuRadar,
    blurb: "Events, compset rates and pricing guardrails for the next 90 days." },
  { key: "inquiries", label: "Inquiries", href: "inquiries", perm: "inquiries.view", module: "inquiries", icon: LuInbox,
    blurb: "Direct booking inquiries, speed to reply and quotes." },
  { key: "amenities", label: "Amenities", href: "amenities", perm: "amenities.view", module: "amenities", icon: LuGift,
    blurb: "In-room amenity use, stock and leakage." },
  { key: "menu", label: "Menu", href: "menu", perm: "menu.view", module: "upsell", icon: LuUtensils,
    blurb: "Outlets, menus, orders and upsell offers." },
  { key: "reviews", label: "Reviews", href: "reviews", perm: "reviews.view", module: "reviews", icon: LuStar,
    blurb: "Review themes and replies across platforms." },
  { key: "guests", label: "Guests", href: "guests", perm: "guests.view", module: "guests", icon: LuUsers,
    blurb: "Consent-aware guest profiles and journeys." },
  { key: "visibility", label: "Visibility", href: "visibility", perm: "visibility.view", module: "visibility", icon: LuEye,
    blurb: "How AI assistants and Google present the hotel." },
  { key: "finance", label: "Finance", href: "finance", perm: "finance.view", module: "finance", icon: LuLandmark,
    blurb: "Monthly P&L pulse, GOPPAR and NRevPAR." },
  { key: "ask", label: "Ask", href: "ask", perm: "ask.view", module: "ask", icon: LuMessageSquare, mobilePrimary: true,
    blurb: "Ask a question of your own data, in plain English." },
  { key: "trust", label: "Trust", href: "trust", perm: "trust.view", module: "trust", icon: LuShieldCheck,
    blurb: "Audit log, privacy requests and AI evaluations." },
  { key: "settings", label: "Settings", href: "settings", perm: "core.settings.view", module: "core", icon: LuSettings,
    blurb: "Hotel profile, team, devices and module settings." },
];

export function navItem(key: NavKey): NavItem {
  const item = NAV.find(n => n.key === key);
  if (!item) throw new Error(`Unknown nav key: ${key}`);
  return item;
}

export function navHref(hotelSlug: string, item: Pick<NavItem, "href">, sub = ""): string {
  return `/app/${encodeURIComponent(hotelSlug)}/${item.href}${sub}`;
}

/** The nav entry a pathname belongs to (first segment after the slug). */
export function activeNavKey(pathname: string | null, hotelSlug: string): NavKey | null {
  if (!pathname) return null;
  const prefix = `/app/${encodeURIComponent(hotelSlug)}/`;
  const alt = `/app/${hotelSlug}/`;
  const rest = pathname.startsWith(prefix) ? pathname.slice(prefix.length) : pathname.startsWith(alt) ? pathname.slice(alt.length) : "";
  const seg = rest.split("/")[0];
  return (NAV.find(n => n.href === seg)?.key ?? null) as NavKey | null;
}

/** Role landing (foundation spec F): where each role starts its day. */
export function landingKey(role: string | null, opts: { isAdmin: boolean; legacyAnonymous: boolean }): NavKey {
  if (opts.legacyAnonymous) return "service";
  if (opts.isAdmin) return "brief";
  switch (role) {
    case "owner":
    case "gm":
      return "brief";
    case "finance":
      return "profit";
    case "revenue":
      return "demand";
    default:
      return "service";
  }
}
