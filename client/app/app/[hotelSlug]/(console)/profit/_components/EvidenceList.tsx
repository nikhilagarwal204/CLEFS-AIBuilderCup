"use client";

/* EvidenceList: what the finding rests on (statement and payout lines,
   the PMS reservation, rate screenshots, contract clauses), through the
   kit's EvidencePanel so every module reads evidence the same way. Row
   references are made readable and their files linked by panelEvidence;
   links open through the authenticated file route. */

import { EvidencePanel } from "../../../../../ui";
import type { Evidence } from "../../../../../lib/types";
import { panelEvidence } from "../lib";

export function EvidenceList({ evidence, slug }: { evidence: readonly Evidence[] | null | undefined; slug: string }) {
  return <EvidencePanel evidence={panelEvidence(evidence)} hotelSlug={slug} empty="No evidence was attached to this finding." />;
}
