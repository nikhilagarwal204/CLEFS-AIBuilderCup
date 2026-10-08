import QuotePage from "./QuotePage";

/* /q/[token]: the guest's quote (04 spec 4.6 step 8). `?lang=ar` opens it
   in Arabic; otherwise the quote's own language wins. Metadata (noindex,
   no-referrer) comes from q/layout. Rendered on the client so the API's
   per-IP rate limit sees the guest, not the Next server. */

type SP = Record<string, string | string[] | undefined>;

export default async function QuoteTokenPage({ params, searchParams }: {
  params: Promise<{ token: string }>;
  searchParams: Promise<SP>;
}) {
  const { token } = await params;
  const sp = await searchParams;
  const lang = sp.lang === "ar" || sp.lang === "en" ? sp.lang : null;
  return <QuotePage token={token} initialLang={lang} />;
}
