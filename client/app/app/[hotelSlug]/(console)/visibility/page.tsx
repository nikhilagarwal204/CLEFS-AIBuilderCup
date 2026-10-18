import VisibilityConsole from "./VisibilityConsole";

/* Server entry for the visibility console. `?tab=` and the OAuth return
   params (`oauth`, `oauth_status`, `oauth_error`, set by the core OAuth
   callback, whose default landing page is this route) are read here and
   handed down as props, so the client console needs no useSearchParams
   (which would force a Suspense boundary and a client bailout). */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

export default async function VisibilityPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const oauth = one(sp.oauth) === "google_business"
    ? { status: one(sp.oauth_status) ?? "error", error: one(sp.oauth_error) }
    : undefined;
  return <VisibilityConsole initialTab={one(sp.tab)} oauthReturn={oauth} />;
}
