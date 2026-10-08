"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One campaign draft (export only): channel, audience size (consented
   direct contacts only; Instagram is an organic post with no list), the
   suggested send time inside 09:00-18:00 Dubai, and the copy. Owners edit
   the copy, copy it to the clipboard, approve, archive or export the CSV.
   Clefs never sends it; an exported draft is final.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { apiFetch, api } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Field, Textarea, useToast } from "../../../../../ui";
import { campaignText, CHANNEL_LABEL, copyFields } from "../_lib/model";
import { drApi, errorText, PERM } from "../_lib/paths";
import type { Campaign } from "../_lib/types";

const FIELD_LABEL: Record<string, string> = { text: "Message", caption: "Caption", subject: "Subject", body: "Body" };
const STATUS_TONE = { DRAFT: "gold", APPROVED: "ok", EXPORTED: "info", ARCHIVED: "neutral" } as const;

export default function CampaignCard({ slug, c, onChanged }: { slug: string; c: Campaign; onChanged: (next?: Campaign) => void }) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const manage = can(PERM.manage);
  const final = c.status === "EXPORTED" || c.status === "ARCHIVED";
  const fields = copyFields(c);

  const patch = async (what: string, body: Record<string, unknown>, ok: string) => {
    setBusy(what);
    try {
      const next = await api.patch<Campaign>(drApi(slug, `/campaigns/${c.id}`), body);
      toast.success(ok);
      setEdit(null);
      onChanged(next);
    } catch (err) {
      toast.error("Could not update the draft", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(campaignText(c));
      toast.success("Copied", `${CHANNEL_LABEL[c.channel]} copy is on your clipboard.`);
    } catch {
      toast.error("Could not copy", "Select the text and copy it by hand.");
    }
  };

  const exportCsv = async () => {
    setBusy("export");
    try {
      const csv = await apiFetch<string>(drApi(slug, `/campaigns/${c.id}/export.csv`), { headers: { Accept: "text/csv" } });
      const url = URL.createObjectURL(new Blob([csv ?? ""], { type: "text/csv;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `campaign-${c.id}-${c.channel.toLowerCase()}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast.success("Exported", "The draft is now final. Send it from your own WhatsApp, email or Instagram account.");
      onChanged();
    } catch (err) {
      toast.error("Export failed", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="dr-item">
      <div className="dr-item__head">
        <span className="dr-item__title">{CHANNEL_LABEL[c.channel]}</span>
        <Badge tone={STATUS_TONE[c.status]}>{c.status.toLowerCase()}</Badge>
      </div>
      <div className="dr-item__meta">
        <span>
          {c.audience.kind === "organic_post"
            ? "Organic post, no contact list"
            : `${c.audience.count ?? 0} consented contacts${c.audience.countries.length ? ` from ${c.audience.countries.join(", ")}` : ""}`}
        </span>
        {c.suggested_send_at && <span>Suggested send {fmt.dateTime(c.suggested_send_at, "medium")} (09:00-18:00 Dubai only)</span>}
        {c.exported_at && <span>Exported {fmt.relative(c.exported_at)}</span>}
      </div>
      {c.audience.note && <p className="dr-note" style={{ marginTop: 4 }}>{c.audience.note}</p>}

      {edit ? (
        <div className="cx-stack" style={{ gap: 8, marginTop: 8 }}>
          {fields.map(k => (
            <Field key={k} label={FIELD_LABEL[k] ?? k}>
              <Textarea rows={k === "subject" ? 1 : 5} value={edit[k] ?? ""} disabled={busy !== null}
                        onChange={e => setEdit(prev => ({ ...(prev ?? {}), [k]: e.target.value }))} />
            </Field>
          ))}
          <div className="cx-row" style={{ gap: 6 }}>
            <Button size="sm" variant="primary" loading={busy === "save"} onClick={() => patch("save", { copy: edit }, "Copy saved")}>Save copy</Button>
            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => setEdit(null)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <pre className="dr-copy" style={{ marginTop: 8 }}>{campaignText(c) || "No copy yet."}</pre>
      )}

      {!edit && (
        <div className="cx-row" style={{ gap: 6, marginTop: 8 }}>
          <Button size="sm" onClick={copy}>Copy text</Button>
          {manage && !final && (
            <>
              <Button size="sm" variant="ghost" onClick={() => setEdit(Object.fromEntries(fields.map(k => [k, (c.copy as Record<string, string | undefined>)[k] ?? ""])))}>Edit</Button>
              {c.status === "DRAFT" && <Button size="sm" variant="ghost" loading={busy === "approve"} onClick={() => patch("approve", { status: "APPROVED" }, "Approved")}>Approve</Button>}
              <Button size="sm" variant="ghost" loading={busy === "archive"} onClick={() => patch("archive", { status: "ARCHIVED" }, "Archived")}>Archive</Button>
              <Button size="sm" variant="primary" loading={busy === "export"} onClick={exportCsv}>Export CSV</Button>
            </>
          )}
        </div>
      )}
    </li>
  );
}
