import TimelineView from "./TimelineView";

/* One reservation's journey timeline. The id is read on the server (async
   params, Next 16) and validated before the client view fetches anything. */
export default async function TimelinePage({ params }: { params: Promise<{ reservationId: string }> }) {
  const { reservationId } = await params;
  const id = Number(reservationId);
  return <TimelineView reservationId={Number.isInteger(id) && id > 0 ? id : null} />;
}
