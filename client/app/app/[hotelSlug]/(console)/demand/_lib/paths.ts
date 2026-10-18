/* ═══════════════════════════════════════════════════════════════════════
   Demand Radar console paths, permissions and error wording. Kept apart
   from model.ts because it imports the foundation api client.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, hotelPath } from "../../../../../lib/api";

// ─── Paths ───────────────────────────────────────────────────────────────

/** API path under /hotels/{slug}/demand (proxied through /api/clefs). */
export const drApi = (slug: string, p = "") => hotelPath(slug, `/demand${p}`);

/** Console route under /app/{slug}/demand. */
export const drHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/demand${p}`;

// ─── Permissions ─────────────────────────────────────────────────────────

export const PERM = { view: "demand.view", manage: "demand.manage" } as const;

// ─── Errors ──────────────────────────────────────────────────────────────

/** Server error codes the owner can act on, in plain words; anything else
    falls back to the server's own sentence. */
const CODE_TEXT: Record<string, string> = {
  rate_out_of_bounds: "That rate is outside the floor and ceiling for this night.",
  recommendation_closed: "Someone already decided this recommendation. The view has been refreshed.",
  event_not_editable: "Only events your team added can be edited or removed.",
  rate_limited: "Too many requests in the last hour. Try again later.",
  file_too_large: "The file is larger than 2 MB.",
  unreadable_file: "The file could not be read. Export it again as .ics or .csv.",
  no_events: "No events were found in that file.",
  no_rates: "No rates were found in that file.",
  comp_exists: "That hotel is already in your comp set.",
  comp_set_full: "The comp set is full (25 hotels). Remove one first.",
  invalid_share: "Source-market shares add up to more than 100%.",
  budget_exhausted: "This month's rate-search budget is used up.",
  not_configured: "Rate search is not set up for this hotel yet.",
  campaign_exported: "This draft was already exported and is final.",
  copy_lint: "The copy makes a promise we cannot keep (for example \"guaranteed\"). Reword it.",
};

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return (err.code && CODE_TEXT[err.code]) || err.message;
  return (err as Error)?.message || "Something went wrong.";
}
