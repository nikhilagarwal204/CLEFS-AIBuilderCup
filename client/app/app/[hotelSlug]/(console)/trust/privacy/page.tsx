"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Privacy (03 spec 5.2-5.3): the privacy request queue (UAE PDPL, 30 days
   from receipt), retention settings with the deletion log, who sees which
   personal data (PII matrix) with the consent wording, and the owner's
   full data export.
   The view lives in ?view= so a link from a trust.dsr_due signal opens the
   queue directly and back returns to the tab the reader was on.
   ═══════════════════════════════════════════════════════════════════════ */

import { useRouter, useSearchParams } from "next/navigation";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Card, EmptyState, Segmented } from "../../../../../ui";
import { trustHref } from "../lib";
import DsrPanel from "./DsrPanel";
import ExportPanel from "./ExportPanel";
import MatrixPanel from "./MatrixPanel";
import RetentionPanel from "./RetentionPanel";

type View = "requests" | "retention" | "access" | "export";

export default function PrivacyPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const router = useRouter();
  const params = useSearchParams();

  const canDsr = can("trust.privacy.manage");
  const canView = can("trust.view");
  const canManage = can("trust.manage");

  const options: { value: View; label: string }[] = [
    ...(canDsr ? [{ value: "requests" as const, label: "Requests" }] : []),
    ...(canView ? [{ value: "retention" as const, label: "Retention" }, { value: "access" as const, label: "Access and consent" }] : []),
    ...(canManage ? [{ value: "export" as const, label: "Data export" }] : []),
  ];
  const asked = params?.get("view") as View | null;
  const view: View | undefined = options.find(o => o.value === asked)?.value ?? options[0]?.value;

  if (!view) {
    return <Card><EmptyState title="Privacy is for owners and GMs" body="Ask the hotel owner if you need access." /></Card>;
  }

  // replace, not push: switching sub-views should not fill the back stack.
  const setView = (v: View) => router.replace(`${trustHref(slug, "/privacy")}?view=${v}`, { scroll: false });

  return (
    <>
      {options.length > 1 && <Segmented ariaLabel="Privacy view" value={view} onChange={setView} options={options} />}
      {view === "requests" && <DsrPanel slug={slug} />}
      {view === "retention" && <RetentionPanel slug={slug} canManage={canManage} />}
      {view === "access" && <MatrixPanel slug={slug} canManage={canManage} />}
      {view === "export" && <ExportPanel slug={slug} />}
    </>
  );
}
