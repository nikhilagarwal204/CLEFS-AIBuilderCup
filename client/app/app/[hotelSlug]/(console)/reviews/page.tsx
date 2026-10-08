import ReviewsConsole from "./ReviewsConsole";

/* Server entry for the reviews console. `?tab=` and the OAuth return params
   (`oauth`, `oauth_status`, `oauth_error`, set by the core OAuth callback on
   its way back from Google) are read here and handed down as props, so the
   client console needs no useSearchParams (which would force a Suspense
   boundary and a client-side bailout for the whole page). */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

export default async function ReviewsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const oauth = one(sp.oauth) === "google_business"
    ? { status: one(sp.oauth_status) ?? "error", error: one(sp.oauth_error) }
    : undefined;
  return <ReviewsConsole initialTab={one(sp.tab)} oauthReturn={oauth} />;
}
