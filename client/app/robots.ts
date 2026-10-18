import type { MetadataRoute } from "next";
import { SITE } from "./lib/seo";

/**
 * Crawl rules. Everything below is private, per-visitor or tokenised, and has
 * no search value; several would leak a hotel's or a guest's context into an
 * index if crawled:
 *   /app/       staff console
 *   /api/       API proxy and legacy routes
 *   /stay/      per-room guest concierge (QR)
 *   /aria/      legacy QR redirect kept for printed codes
 *   /device/    shared floor and department devices
 *   /g/ /q/ /w/ guest journey links, quote links, inquiry widget
 *   /brief/     owner brief magic links
 *   /privacy/request      the rights form (reached from a hotel's link)
 *   /google-check/r/      one visitor's check result (04 section 2.4)
 *   /free-ota-audit/r/    one visitor's OTA audit report (tokenised)
 * Disallow stops crawling only; each of those pages also sets noindex.
 */
const PRIVATE = [
  "/app/", "/api/", "/stay/", "/aria/", "/device/",
  "/g/", "/q/", "/w/", "/brief/",
  "/privacy/request", "/google-check/r/", "/free-ota-audit/r/",
];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: PRIVATE }],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE,
  };
}
