import ImportsView from "../_components/ImportsView";

/* Profit Guard imports. `?batch=<id>` opens one import (the brief's
   "needs review" signal links here); it is read on the server and handed
   down so the client needs no useSearchParams. */

type SP = Record<string, string | string[] | undefined>;

export default async function ProfitImportsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const open = typeof sp.batch === "string" && /^\d+$/.test(sp.batch) ? Number(sp.batch) : undefined;
  return <ImportsView initialBatch={open} />;
}
