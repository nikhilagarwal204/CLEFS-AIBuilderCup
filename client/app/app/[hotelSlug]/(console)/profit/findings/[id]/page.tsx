import FindingDetail from "../../_components/FindingDetail";

/* One finding: trace, evidence, decision and its ledger (02 spec 3.10). */
export default async function FindingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <FindingDetail id={id} />;
}
