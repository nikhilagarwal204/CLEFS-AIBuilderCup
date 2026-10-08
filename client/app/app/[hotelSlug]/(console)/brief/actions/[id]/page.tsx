import { notFound } from "next/navigation";
import ActionPage from "./ActionPage";

/* /brief/actions/{id}: one action with its StateTimeline. The brief builds
   this link as an item's evidence fallback, so it must stand on its own. */

export default async function BriefActionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d{1,12}$/.test(id)) notFound();
  return <ActionPage actionId={Number(id)} />;
}
