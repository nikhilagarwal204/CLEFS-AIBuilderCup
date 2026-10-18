import InquiryForm, { type Attribution } from "./InquiryForm";

/* /w/inquiry/[slug]?k=<widget key>: the hotel's inquiry form, framed by
   /w/inquiry.js on the hotel's website (or pasted as a plain iframe).
   The loader forwards what only the host page knows: its URL (page_url),
   its utm_* and gclid, the language, theme and accent. Everything is read
   here and handed down as props (no useSearchParams, so no Suspense
   bailout), and each value is length-capped before it reaches the form. */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined, max = 300) => (typeof v === "string" ? v.slice(0, max) : undefined);

export default async function InquiryWidgetPage({ params, searchParams }: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SP>;
}) {
  const { slug } = await params;
  const sp = await searchParams;
  const utm: Record<string, string> = {};
  for (const [k, v] of Object.entries(sp)) {
    const s = one(v, 200);
    if (k.startsWith("utm_") && k.length <= 40 && s) utm[k] = s;
  }
  const attribution: Attribution = {
    page_url: one(sp.page_url, 2000),
    gclid: one(sp.gclid),
    utm: Object.keys(utm).length ? utm : undefined,
  };
  const accent = one(sp.accent, 7);
  return (
    <InquiryForm
      slug={slug}
      widgetKey={one(sp.k, 100) ?? ""}
      initialLang={one(sp.lang, 10) ?? null}
      theme={one(sp.theme) === "dark" ? "dark" : "light"}
      accent={accent && /^#[0-9a-fA-F]{6}$/.test(accent) ? accent : null}
      frameId={one(sp.fid, 40) ?? null}
      attribution={attribution}
    />
  );
}
