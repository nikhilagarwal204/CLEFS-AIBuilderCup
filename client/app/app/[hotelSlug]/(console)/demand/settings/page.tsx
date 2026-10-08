"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Demand settings (02 spec 4.4): the Google Hotel Center checklist first
   (its deadline is the nearest), then the comp set, rate guardrails with
   the BAR import, and source markets. Everything here writes, so the page
   needs demand.manage; the server enforces the same.
   ═══════════════════════════════════════════════════════════════════════ */

import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { EmptyState } from "../../../../../ui";
import CompSetPanel from "../_components/CompSetPanel";
import GhcPanel from "../_components/GhcPanel";
import GuardrailsPanel from "../_components/GuardrailsPanel";
import SourceMarketsPanel from "../_components/SourceMarketsPanel";
import { PERM } from "../_lib/paths";

export default function DemandSettingsPage() {
  const { slug } = useHotel();
  const { can, loading } = useSession();
  if (!loading && !can(PERM.manage)) {
    return <EmptyState title="Settings need demand.manage" body="Ask the owner or GM to change the comp set, guardrails or source markets." />;
  }
  return (
    <>
      <GhcPanel slug={slug} />
      <CompSetPanel slug={slug} />
      <GuardrailsPanel slug={slug} />
      <SourceMarketsPanel slug={slug} />
    </>
  );
}
