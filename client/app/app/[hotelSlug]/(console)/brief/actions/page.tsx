import ActionsList from "./ActionsList";

/* Server entry for the actions list. ?overdue=true opens the overdue filter
   (the escalation messages link here); ?open={id} opens that action's drawer. */

type SP = Record<string, string | string[] | undefined>;

export default async function BriefActionsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const open = typeof sp.open === "string" && /^\d{1,12}$/.test(sp.open) ? Number(sp.open) : null;
  return <ActionsList initialOverdue={sp.overdue === "true" || sp.overdue === "1"} initialOpenId={open} />;
}
