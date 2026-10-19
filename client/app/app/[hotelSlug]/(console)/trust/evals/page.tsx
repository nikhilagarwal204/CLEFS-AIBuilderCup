"use client";

/* ═══════════════════════════════════════════════════════════════════════
   AI evaluations (03 spec 5.4): the readiness gate that controls turning
   the concierge live, run history with per-category pass rates, and the
   scenario library. `concierge.manage` may read the gate (it decides the
   go-live switch) but runs and scenarios need `trust.evals.view`.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Card, EmptyState, Segmented } from "../../../../../ui";
import { trustApi } from "../lib";
import type { Readiness } from "../types";
import ReadinessCard from "./ReadinessCard";
import RunsPanel from "./RunsPanel";
import ScenariosPanel from "./ScenariosPanel";

export default function EvalsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const canGate = can(["trust.evals.view", "concierge.manage"]);
  const canView = can("trust.evals.view");
  const readiness = useApi<Readiness>(canGate ? trustApi(slug, "/readiness") : null, [slug]);
  const [view, setView] = useState<"runs" | "scenarios">("runs");

  if (!canGate) {
    return <Card><EmptyState title="The quality gate is for owners, GMs and department heads" body="Ask the hotel owner if you need access." /></Card>;
  }
  return (
    <>
      <ReadinessCard slug={slug} res={readiness} />
      {canView && (
        <>
          <Segmented ariaLabel="Evaluation view" value={view} onChange={setView}
                     options={[{ value: "runs", label: "Runs" }, { value: "scenarios", label: "Scenarios" }]} />
          {view === "runs" ? <RunsPanel slug={slug} onRunChanged={readiness.reload} /> : <ScenariosPanel slug={slug} />}
        </>
      )}
    </>
  );
}
