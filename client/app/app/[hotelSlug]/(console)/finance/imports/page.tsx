import ImportsConsole from "../_components/ImportsConsole";
import { readOAuthReturn } from "../lib";

/* P&L imports: upload, accounting connections, and every imported month
   with its checks and the publish button. Connect sends the core OAuth
   flow back here (next=/finance/imports), so the return params are read
   on the server and handed down, as on the overview. */

type SP = Record<string, string | string[] | undefined>;

export default async function FinanceImportsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const open = typeof sp.batch === "string" && /^\d+$/.test(sp.batch) ? Number(sp.batch) : undefined;
  return <ImportsConsole oauthReturn={readOAuthReturn(sp)} initialBatch={open} />;
}
