"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Printable pilot ROI report (03 spec 5.5). An owner prints it or saves a
   PDF for a bank or a management company, so it is a document: A4, black
   on white in print (styles.ts @media print), no console chrome, and every
   figure carries a footnote naming the metric query behind it.
   Money is the hotel's currency (AED for UAE hotels) from BIGINT minor
   units, formatted here and never recomputed: the server did the maths.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useApi } from "../../../../../../../lib/api";
import { useHotel } from "../../../../../../../lib/hotel";
import { useSession } from "../../../../../../../lib/session";
import { Button, Card, EmptyState, Skeleton } from "../../../../../../../ui";
import { trustApi, trustHref } from "../../../lib";
import type { PilotReport } from "../../../types";
import ReportBody from "./ReportBody";

export default function PilotReportPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const params = useParams<{ id: string }>();
  const id = Number(params?.id);
  const valid = Number.isInteger(id) && id > 0;
  const allowed = can("trust.pilots.view");
  const res = useApi<PilotReport>(allowed && valid ? trustApi(slug, `/pilots/${id}/report`) : null, [slug, id]);

  if (!allowed) return <Card><EmptyState title="Pilot reports are for owners, GMs and finance" /></Card>;
  if (!valid) return <Card><EmptyState title="Pilot not found" /></Card>;
  if (res.error) {
    return (
      <Card>
        <EmptyState title={res.error.status === 404 ? "Pilot not found" : "Could not build the report"} body={res.error.message}
                    action={<Button variant="secondary" size="sm" onClick={res.reload}>Try again</Button>} />
      </Card>
    );
  }

  return (
    <>
      <div className="cx-row trs-noprint" style={{ gap: 8, justifyContent: "space-between", flexWrap: "wrap" }}>
        <Link href={trustHref(slug, `/pilots/${id}`)} className="trs-small">Back to the pilot</Link>
        <div className="cx-row" style={{ gap: 8 }}>
          <Button size="sm" variant="secondary" onClick={res.reload} disabled={res.loading}>Refresh</Button>
          <Button size="sm" onClick={() => window.print()} disabled={!res.data}>Print or save as PDF</Button>
        </div>
      </div>
      {res.data ? <ReportBody slug={slug} r={res.data} /> : <div className="trs-report"><Skeleton lines={14} /></div>}
    </>
  );
}
