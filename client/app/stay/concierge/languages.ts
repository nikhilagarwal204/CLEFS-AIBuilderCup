/* ═══════════════════════════════════════════════════════════════════════
   Which guest languages this hotel offers. Pure (no runtime imports) so
   node --test can load it; the caller passes the full supported list.

   WHY: the picker and the footer listed all eight UI languages although a
   hotel enables its own subset (hotel.guest_languages, e.g. en/ar/ru/hi/zh),
   so a guest could pick a language the hotel's staff and prompts do not
   cover (QA GS-9). The guest's CURRENT language always stays listed, so a
   ?lang=de link never leaves the picker unable to show what is active.
   ═══════════════════════════════════════════════════════════════════════ */

/** Hotel languages (normalised to the base tag, supported ones only, in the
    hotel's order), plus the current one; every supported language when the
    hotel configured none. */
export function offeredLanguages<L extends string>(
  preferred: readonly string[] | null | undefined,
  supported: readonly L[],
  current?: string | null,
): L[] {
  const isSupported = (v: string): v is L => (supported as readonly string[]).includes(v);
  const base = (v: string) => v.split(/[-_]/)[0].trim().toLowerCase();
  const hotel = (preferred ?? []).map(base).filter(isSupported);
  if (hotel.length === 0) return [...supported];
  const cur = current ? base(current) : "";
  const out = [...new Set<L>(hotel)];
  if (isSupported(cur) && !out.includes(cur)) out.push(cur);
  return out;
}
