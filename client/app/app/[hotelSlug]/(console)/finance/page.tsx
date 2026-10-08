import FinanceOverview from "./_components/FinanceOverview";
import { readOAuthReturn } from "./lib";

/* Server entry for the finance overview. `?month=YYYY-MM` and the OAuth
   return params (`oauth`, `oauth_status`, `oauth_error`: the core callback
   lands on /finance by default) are read here and handed down as props, so
   the client needs no useSearchParams (which would force a Suspense
   boundary and a client-side bailout for the whole page). */

type SP = Record<string, string | string[] | undefined>;

export default async function FinancePage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const month = typeof sp.month === "string" ? sp.month : undefined;
  return <FinanceOverview initialMonth={month} oauthReturn={readOAuthReturn(sp)} />;
}
