/* Where the widget is embedded, and whether the hotel allows it there.

   The real guard is server-side (the widget key, rate limits, consents).
   A per-hotel `frame-ancestors` header would stop other sites framing the
   form at all, but pages cannot set response headers and proxy.ts is
   frozen (FOUNDATION-REQUEST in the lane report), so this is the client
   half: when the hotel listed allowed origins, the form hides itself on
   any other embedding site. Pure functions, no imports, so node --test
   loads this file directly. */

/** The embedding page's origin: Chromium and Safari give it exactly
    (location.ancestorOrigins); elsewhere the iframe's referrer (the host
    page, which sends at least its origin under the default policy). Null
    when not framed or unknown. */
export function embedderOrigin(
  framed: boolean,
  ancestorOrigins: ArrayLike<string> | null | undefined,
  referrer: string | null | undefined,
): string | null {
  if (!framed) return null;
  if (ancestorOrigins && ancestorOrigins.length > 0 && ancestorOrigins[0]) return normalizeOrigin(ancestorOrigins[0]);
  if (referrer) return normalizeOrigin(referrer);
  return null;
}

export function normalizeOrigin(value: string): string | null {
  try {
    const u = new URL(value);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    return `${u.protocol}//${u.host}`.toLowerCase();
  } catch {
    return null;
  }
}

/** An empty list means "anywhere" (the hotel has not restricted it yet).
    A framed widget whose host cannot be determined is allowed too: the
    referrer can be stripped by the host's own policy, and refusing would
    break real hotel sites while stopping no attacker (the key is public). */
export function originAllowed(origin: string | null, allowed: readonly string[] | null | undefined): boolean {
  const list = (allowed ?? []).map(normalizeOrigin).filter((o): o is string => !!o);
  if (list.length === 0 || origin === null) return true;
  return list.includes(origin.toLowerCase());
}

/** Height message the loader listens for (and the conversion ping). `fid`
    tells several widgets on one page apart. */
export interface WidgetMessage {
  source: "clefs-inquiry";
  type: "height" | "submitted";
  fid: string | null;
  height?: number;
}
