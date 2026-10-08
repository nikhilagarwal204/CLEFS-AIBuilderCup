import BriefToday from "./BriefToday";
import type { Cadence } from "./types";

/* Server entry for today's brief. The query is read here and handed down
   as props, so the client page needs no useSearchParams (which would force
   a Suspense boundary and a client-side bailout for the whole page):
   - ?run={id}: a stored run (the link every brief email and WhatsApp
     "Open brief" button carries after the magic-link exchange);
   - ?cadence=daily|weekly|monthly: another tier's latest;
   - ?signal={id} / ?action={id}: scroll to and outline that item (the
     evidence fallback link the brief builds for a signal). */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

// Kept here, not imported from ./lib: lib pulls in the API client (React
// hooks), which a server component module graph must not import.
const isCadence = (v: unknown): v is Cadence => v === "daily" || v === "weekly" || v === "monthly";

function intOrNull(v: string | undefined): number | null {
  if (!v || !/^\d{1,12}$/.test(v)) return null;
  return Number(v);
}

export default async function BriefPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const cadence = one(sp.cadence);
  return (
    <BriefToday
      key={one(sp.run) ?? "latest"}
      runId={intOrNull(one(sp.run))}
      initialCadence={isCadence(cadence) ? cadence : "daily"}
      focusSignal={intOrNull(one(sp.signal))}
      focusAction={intOrNull(one(sp.action))}
    />
  );
}
