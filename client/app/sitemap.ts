import type { MetadataRoute } from "next";
import { SITE } from "./lib/seo";

/**
 * Every publicly indexable URL. Private and per-visitor surfaces are absent on
 * purpose and disallowed in robots.ts: /app/* (console), /stay/* (guest pages),
 * /device/*, the tokenised /g/, /q/, /w/ and /brief/ links, /privacy/request,
 * the per-hotel /privacy/<slug> notices and /google-check/r/<token> results.
 *
 * Priorities: the homepage, then the three products and the two lead magnets
 * (what an owner or GM should land on), then the pilot and trust pages, then
 * the four older Service Control guides, which keep their URLs because they
 * hold ranking equity.
 */
const PAGES: { path: string; priority: number; freq?: "weekly" | "monthly" }[] = [
  // The site root carries the live demo and the whole story; /story and /try
  // redirect to it (next.config.mjs). The Profit Guard, Demand Radar and
  // Google check pages redirect home too while their modules are switched off.
  { path: "/", priority: 1 },
  { path: "/service-control", priority: 0.9 },
  { path: "/pilot", priority: 0.8 },
  // Trust
  { path: "/security", priority: 0.7, freq: "monthly" },
  { path: "/subprocessors", priority: 0.4, freq: "monthly" },
  { path: "/dpa", priority: 0.4, freq: "monthly" },
  // Service Control guides
  { path: "/ai-concierge-software", priority: 0.7 },
  { path: "/guest-request-management", priority: 0.7 },
  { path: "/hotel-front-desk-automation", priority: 0.7 },
  { path: "/multilingual-guest-messaging", priority: 0.7 },
];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return PAGES.map(({ path, priority, freq }) => ({
    url: `${SITE}${path === "/" ? "" : path}`,
    lastModified,
    changeFrequency: freq ?? ("weekly" as const),
    priority,
  }));
}
