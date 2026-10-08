"use client";

/* WhatsApp quote templates (4.6): outside the 24 h window only a template
   Meta approved can carry a quote or a follow-up. The owner approves the
   wording here first, then Clefs submits it to Meta; Meta decides the
   category, and one it treats as marketing is only sent 09:00 to 18:00
   Dubai time. Until both approvals exist, quotes outside the window go by
   email. */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Skeleton, useToast, type Tone } from "../../../../../ui";
import { errorText, inqPath } from "../lib";

interface TemplateRow {
  id: number;
  name: "inq_quote_ready_v1" | "inq_quote_followup_v1" | string;
  language: "en" | "ar";
  body: string;
  version: number;
  meta_status: string | null;
  category_meta: string | null;
  approved_at: string | null;
  submitted_at: string | null;
}

const NAME_TEXT: Record<string, string> = {
  inq_quote_ready_v1: "Quote ready",
  inq_quote_followup_v1: "Quote follow-up",
};

function metaBadge(s: string | null): { tone: Tone; text: string } {
  switch ((s || "").toUpperCase()) {
    case "APPROVED": return { tone: "ok", text: "Meta approved" };
    case "PENDING": case "IN_APPEAL": return { tone: "info", text: "Meta reviewing" };
    case "REJECTED": case "DISABLED": case "PAUSED": return { tone: "bad", text: `Meta ${s!.toLowerCase()}` };
    case "NOT_CONNECTED": return { tone: "warn", text: "WhatsApp not connected" };
    case "SUBMIT_FAILED": return { tone: "bad", text: "Submission failed" };
    case "": return { tone: "neutral", text: "Not submitted" };
    default: return { tone: "neutral", text: s! };
  }
}

export default function TemplatesTab({ canManage }: { canManage: boolean }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const rows = useApi<TemplateRow[]>(inqPath(slug, "/templates"));
  const [busy, setBusy] = useState<number | null>(null);

  async function approve(id: number) {
    setBusy(id);
    try {
      await api.post(inqPath(slug, `/templates/${id}/approve`));
      rows.reload();
      toast.success("Approved", "Submitted to Meta for review.");
    } catch (err) {
      toast.error("Not approved", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  if (rows.loading && !rows.data) return <Skeleton lines={6} height={16} />;
  if (rows.error) return <Card><p className="inq-muted">{rows.error.message}</p><Button onClick={rows.reload}>Try again</Button></Card>;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {(rows.data ?? []).map(r => {
        const m = metaBadge(r.meta_status);
        return (
          <Card key={r.id} title={`${NAME_TEXT[r.name] ?? r.name} (${r.language === "ar" ? "Arabic" : "English"})`}
            subtitle={`Version ${r.version}${r.category_meta ? `, Meta category ${r.category_meta.toLowerCase()}` : ""}`}
            actions={
              <span className="inq-row">
                {r.approved_at ? <Badge tone="ok">You approved</Badge> : <Badge tone="warn">Needs your approval</Badge>}
                <Badge tone={m.tone}>{m.text}</Badge>
              </span>
            }>
            <pre className="inq-code" dir={r.language === "ar" ? "rtl" : "ltr"} lang={r.language}>{r.body}</pre>
            <div className="inq-row" style={{ marginTop: 10 }}>
              {canManage && !r.approved_at && (
                <Button variant="primary" loading={busy === r.id} onClick={() => approve(r.id)}>Approve and submit to Meta</Button>
              )}
              {canManage && r.approved_at && (r.meta_status || "").toUpperCase() !== "APPROVED" && (
                <Button loading={busy === r.id} onClick={() => approve(r.id)}>Submit again</Button>
              )}
              {r.approved_at && <span className="inq-muted">Approved {fmt.dateTime(r.approved_at, "medium")}</span>}
            </div>
          </Card>
        );
      })}
      {(rows.data ?? []).length === 0 && <p className="inq-muted">No templates yet.</p>}
    </div>
  );
}
