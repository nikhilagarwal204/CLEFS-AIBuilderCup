import InquiriesConsole from "./InquiriesConsole";

/* Server entry for the Inquiry Guard console (04 spec 4.11). `?id=` opens
   one inquiry's drawer directly (the brief's "2 unanswered" line and the
   approval cards link here), and `?sla=breached` lands on the overdue list.
   Both are read here and handed down as props, so the client console needs
   no useSearchParams (which would force a Suspense boundary and a
   client-side bailout for the whole page). */

type SP = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);

export default async function InquiriesPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const id = Number(one(sp.id));
  const sla = one(sp.sla);
  return (
    <InquiriesConsole
      initialId={Number.isInteger(id) && id > 0 ? id : null}
      initialSla={sla === "breached" || sla === "due" || sla === "answered" ? sla : ""}
    />
  );
}
