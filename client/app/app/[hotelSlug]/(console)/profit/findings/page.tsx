import FindingsTable, { type FindingFilters } from "../_components/FindingsTable";

/* Findings list. Filters can arrive in the URL (?status=&detector=&ota=
   &class=) so a brief link or a signal lands pre-filtered; they are read
   here on the server and handed down, so the client needs no
   useSearchParams (which would force a Suspense boundary). */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

export default async function FindingsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const initial: Partial<FindingFilters> = {};
  const status = one(sp.status);
  if (status !== undefined) initial.status = status.toUpperCase() === "ALL" ? "" : status;
  if (one(sp.detector)) initial.detector = one(sp.detector);
  if (one(sp.ota)) initial.ota = one(sp.ota)?.toUpperCase();
  if (one(sp.class)) initial.klass = one(sp.class)?.toUpperCase();
  return <FindingsTable initial={initial} />;
}
