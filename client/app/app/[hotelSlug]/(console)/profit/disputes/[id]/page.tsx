import DisputeDetail from "../../_components/DisputeDetail";

/* One dispute packet: preview, approve, send, outcome (02 spec 3.7). */
export default async function DisputePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DisputeDetail id={id} />;
}
