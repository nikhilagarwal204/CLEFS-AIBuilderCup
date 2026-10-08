import MappingPage from "../_components/MappingPage";

/* Account mapping review (04 spec 6.8 mapping/). `?batch_id=` narrows the
   list to one import (the batch drawer links here) and `?status=` picks
   the filter. Read on the server and handed down as props, like the other
   finance pages, so the client needs no useSearchParams. */

type SP = Record<string, string | string[] | undefined>;

export default async function FinanceMappingPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const b = typeof sp.batch_id === "string" && /^\d+$/.test(sp.batch_id) ? Number(sp.batch_id) : undefined;
  const s = sp.status;
  const status = s === "confirmed" || s === "all" || s === "open" ? s : undefined;
  return <MappingPage batchId={b} status={status} />;
}
