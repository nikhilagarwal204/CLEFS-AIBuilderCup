import AskConsole from "./AskConsole";

/* Server entry for Ask Clefs. The query is read here and handed down as
   props, so the client console needs no useSearchParams (which would force
   a Suspense boundary and a client-side bailout for the whole page):
   - ?log={id}: one stored answer, the link every WhatsApp Ask reply carries
     (after the magic-link exchange); opened without a new model call;
   - ?view={id}: run one of the viewer's saved views straight away.
   Ids are digits only; anything else is ignored rather than sent on. */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

function intOrNull(v: string | undefined): number | null {
  if (!v || !/^\d{1,12}$/.test(v)) return null;
  return Number(v);
}

export default async function AskPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const log = intOrNull(one(sp.log));
  const view = log === null ? intOrNull(one(sp.view)) : null;
  return <AskConsole key={`${log ?? ""}:${view ?? ""}`} initialLogId={log} initialViewId={view} />;
}
