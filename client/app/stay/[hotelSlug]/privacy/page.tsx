import { permanentRedirect } from "next/navigation";
import { privacyNoticeHref } from "../../../privacy/links";

/**
 * /stay/<hotelSlug>/privacy: the path 03 spec 5.3 gives the per-hotel guest
 * privacy notice. The notice is served at /privacy/<hotelSlug> (outside the
 * noindex, per-room /stay tree, and shared with the rights form); this alias
 * keeps the spec path working for anything printed or linked against it.
 *
 * WHY a static segment here is safe: it takes precedence over the sibling
 * [roomId] route, whose non-numeric values mean a "location" concierge
 * (lobby, pool). No hotel names a location "privacy", and before this file
 * the path opened a concierge for a location that does not exist.
 */

type Props = {
  params: Promise<{ hotelSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function StayPrivacyAlias({ params, searchParams }: Props) {
  const [{ hotelSlug }, sp] = await Promise.all([params, searchParams]);
  const lang = Array.isArray(sp.lang) ? sp.lang[0] : sp.lang;
  permanentRedirect(privacyNoticeHref(hotelSlug.toLowerCase(), lang));
}
