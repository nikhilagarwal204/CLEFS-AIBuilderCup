"use client";

/* ═══════════════════════════════════════════════════════════════════════
   "Asked but not answered" (03 spec 2.5 console, kb_unknowns). Every time
   the concierge had no approved answer it escalated with reason
   unknown_answer; the server groups those questions (fuzzy match at insert,
   hourly AI regrouping) into one row per question with a few samples.

   Most-asked first, because five guests asking where the gym is this week
   is the knowledge gap that costs the most front-desk calls. Answering one
   creates a knowledge entry (approved at once if the manager says so) and
   closes the concierge.kb_gap signal on the owner's brief.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { LuHelpCircle } from "react-icons/lu";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Segmented, Skeleton, useToast } from "../../../../../ui";
import { LANGUAGE_NAMES, PERM, ccApi, errText, redactContact } from "../lib";
import type { KbOverview, KbUnknown } from "../types";
import { AnswerModal } from "./AnswerModal";

type Status = KbUnknown["status"];

export default function UnknownsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const manage = can(PERM.manage);
  const [status, setStatus] = useState<Exclude<Status, "merged">>("open");
  const [answering, setAnswering] = useState<KbUnknown | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const list = useApi<KbUnknown[]>(withQuery(ccApi(slug, "/kb/unknowns"), { status }), [slug, status]);
  const kb = useApi<KbOverview>(manage ? ccApi(slug, "/kb") : null, [slug]);

  const rows = useMemo(
    () => [...(list.data ?? [])].sort((a, b) => b.count_7d - a.count_7d || b.count - a.count || b.last_at.localeCompare(a.last_at)),
    [list.data],
  );

  async function dismiss(u: KbUnknown) {
    setBusy(u.id);
    try {
      await api.post(ccApi(slug, `/kb/unknowns/${u.id}/dismiss`));
      toast.success("Dismissed", "It will come back if guests keep asking.");
      list.reload();
    } catch (e) {
      toast.error("Could not dismiss", errText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="cc-spread">
        <p className="cc-sub" style={{ margin: 0, maxWidth: 640 }}>
          Questions guests asked that the concierge had no approved answer for. It handed each one to a person; answer it
          once here and it answers the next guest itself.
        </p>
        <Segmented size="sm" ariaLabel="Status" value={status} onChange={setStatus}
          options={[{ value: "open", label: "Open" }, { value: "answered", label: "Answered" }, { value: "dismissed", label: "Dismissed" }]} />
      </div>
      {list.error && <div className="cc-callout" role="alert">{errText(list.error)}</div>}
      {list.loading && !list.data ? <Skeleton lines={6} /> : rows.length === 0 ? (
        <Card><EmptyState icon={<LuHelpCircle />} title={status === "open" ? "Nothing waiting" : "None yet"}
          body={status === "open" ? "Every question guests asked lately had an approved answer." : undefined} /></Card>
      ) : rows.map(u => (
        <Card key={u.id} title={redactContact(u.title)}
          subtitle={`Asked ${u.count} time${u.count === 1 ? "" : "s"}${u.count_7d ? `, ${u.count_7d} this week` : ""}. Last ${fmt.relative(u.last_at)}.`}
          actions={manage && u.status === "open" ? (
            <div className="cx-row">
              <Button size="sm" onClick={() => setAnswering(u)}>Answer</Button>
              <Button size="sm" variant="ghost" loading={busy === u.id} onClick={() => void dismiss(u)}>Dismiss</Button>
            </div>
          ) : u.kb_entry_id ? <Badge tone="ok" dot>Answered</Badge> : undefined}>
          <div className="cx-stack" style={{ gap: 8 }}>
            {u.samples.length > 0 && (
              <ul className="cc-samples">
                {u.samples.slice(0, 5).map((s, i) => (
                  <li key={i} dir="auto">{redactContact(String(s))}</li>
                ))}
              </ul>
            )}
            <div className="cx-row">
              {u.languages.map(l => <Badge key={l}>{LANGUAGE_NAMES[l] ?? l}</Badge>)}
              {u.count_7d >= 5 && <Badge tone="warn" dot>On the owner brief</Badge>}
            </div>
          </div>
        </Card>
      ))}
      {answering && (
        <AnswerModal slug={slug} unknown={answering} sections={kb.data?.sections ?? []}
          onClose={() => setAnswering(null)} onDone={() => { setAnswering(null); list.reload(); }} />
      )}
    </>
  );
}
