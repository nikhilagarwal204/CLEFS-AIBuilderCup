/* ═══════════════════════════════════════════════════════════════════════
   Guest data for the guest page, via the stay context endpoint only.

   History: this module used to download the whole guest roster to any
   phone that held one room's QR code, then format a GUEST PROFILE block
   (names, ages, loyalty tier, stay dates) into a browser-built AI prompt.
   Both are gone:
   - the server builds the prompt (03 spec 2.1) and decides what personal
     context a session may use (verified room only, first name only);
   - the page reads GET /public/stay/{slug}/{target}/context, which returns
     at most the verified guest's first name, never another room.

   Staff-side guest profiles live in the console Guests module.
   ═══════════════════════════════════════════════════════════════════════ */

import { fetchStayContext, type StayContext } from "../stay/concierge/api";

// ─── Types ───────────────────────────────────────────────────────────────

/** What the guest page may know about its own room. */
export interface GuestRoom {
  roomNumber: string;
  verified: boolean;
  /** Only for a verified room; never a family name. */
  firstName: string | null;
  floor: string | number | null;
}

// ─── Reads ───────────────────────────────────────────────────────────────

// Concurrent calls for the same room share one request: the endpoint is
// rate-limited per IP and target.
const pending = new Map<string, Promise<StayContext | null>>();

function currentKey(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("k");
}

/** The stay context for a room (or venue), or null when it cannot be read. */
export function getStayContext(target: string | number, hotelSlug?: string | null, lang?: string | null): Promise<StayContext | null> {
  if (!hotelSlug) return Promise.resolve(null);
  const k = currentKey();
  const key = `${hotelSlug}/${target}?k=${k || ""}&lang=${lang || ""}`;
  const hit = pending.get(key);
  if (hit) return hit;
  const req = fetchStayContext(hotelSlug, String(target), { k, lang }).catch(() => null);
  pending.set(key, req);
  void req.finally(() => pending.delete(key));
  return req;
}

/** Back-compat shape for callers that only need the room. */
export async function getRoomData(roomNumber: string | number, hotelSlug?: string | null): Promise<GuestRoom | null> {
  const ctx = await getStayContext(roomNumber, hotelSlug);
  if (!ctx?.room) return null;
  return {
    roomNumber: String(ctx.room.number),
    verified: ctx.verified,
    firstName: ctx.guest?.first_name ?? null,
    floor: ctx.room.floor ?? null,
  };
}
