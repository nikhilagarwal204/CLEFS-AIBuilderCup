import PrintView from "../../../_components/PrintView";

/* Print route for a dispute packet (02 spec 3.10): the server's print HTML
   in a frame with a Print button. The profit layout drops its header and
   tabs on this route. */
export default async function DisputePrintPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PrintView id={id} />;
}
