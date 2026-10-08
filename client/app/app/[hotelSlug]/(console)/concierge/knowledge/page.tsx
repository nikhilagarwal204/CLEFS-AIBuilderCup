"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Knowledge editor (03 spec 2.5 console). A section rail on the left (with
   a warning dot on each required section that has no approved answer yet),
   the answers of the chosen section on the right, filtered by status or
   searched, and the import card below.

   Everything a guest hears comes from here. Pending answers show first
   within a section so a manager's review queue is at the top; approving is
   one tap on the card, editing opens the side-by-side EN/AR drawer.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useMemo, useState } from "react";
import { LuBookOpen, LuPlus, LuSearch } from "react-icons/lu";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, Input, Segmented, Skeleton, useToast } from "../../../../../ui";
import { PERM, ccApi, errText } from "../lib";
import type { KbEntry, KbOverview, KbStatus } from "../types";
import { EntryCard } from "./EntryCard";
import { EntryEditor } from "./EntryEditor";
import { ImportPanel } from "./ImportPanel";
import { SectionRail } from "./SectionRail";

type StatusFilter = "all" | KbStatus;

export default function KnowledgePage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const manage = can(PERM.manage);
  const [sectionId, setSectionId] = useState<number | null>(null);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<KbEntry | null | undefined>(undefined);

  const overview = useApi<KbOverview>(ccApi(slug, "/kb"), [slug]);
  const entriesPath = withQuery(ccApi(slug, "/kb/entries"), {
    section_id: sectionId ?? undefined,
    status: status === "all" ? undefined : status,
    q: q.trim() || undefined,
  });
  const entries = useApi<KbEntry[]>(entriesPath, [entriesPath]);
  const reloadOverview = overview.reload;
  const reloadEntries = entries.reload;
  const refresh = useCallback(() => { reloadOverview(); reloadEntries(); }, [reloadOverview, reloadEntries]);

  // Withdrawn and rejected answers only show when asked for; within the
  // rest, the review queue (pending) comes first.
  const rows = useMemo(() => {
    const list = (entries.data ?? []).filter(e => status !== "all" || (e.status !== "archived" && e.status !== "rejected"));
    const rank: Record<KbStatus, number> = { pending: 0, approved: 1, rejected: 2, archived: 3 };
    return [...list].sort((a, b) => rank[a.status] - rank[b.status] || b.updated_at.localeCompare(a.updated_at));
  }, [entries.data, status]);

  async function quickApprove(e: KbEntry) {
    try {
      await api.post(ccApi(slug, `/kb/entries/${e.id}/approve`));
      toast.success("Approved. Guests hear it from now on.");
      refresh();
    } catch (err) {
      toast.error("Could not approve", errText(err));
    }
  }

  if (overview.error) {
    return <Card><EmptyState icon={<LuBookOpen />} title="Could not load the knowledge base" body={errText(overview.error)}
      action={<Button onClick={refresh}>Try again</Button>} /></Card>;
  }
  const sections = overview.data?.sections ?? [];
  const section = sections.find(s => s.id === sectionId) ?? null;

  return (
    <>
      <div className="cc-kb">
        <Card padded>
          {overview.loading && !overview.data ? <Skeleton lines={8} /> : (
            <SectionRail sections={sections} missing={overview.data?.missing_required ?? []} value={sectionId} onChange={setSectionId} />
          )}
        </Card>
        <div className="cx-stack">
          <div className="cc-spread">
            <div className="cc-grow">
              <h2 className="cc-h3">{section ? section.title_en : "All answers"}</h2>
              {section?.title_ar && <p className="cc-sub cc-rtl" dir="rtl" style={{ textAlign: "left" }}>{section.title_ar}</p>}
            </div>
            {manage && <Button icon={<LuPlus />} onClick={() => setEditing(null)}>New answer</Button>}
          </div>
          <div className="cc-spread">
            <Segmented size="sm" ariaLabel="Status" value={status} onChange={setStatus} options={[
              { value: "all", label: "Current" }, { value: "pending", label: "Waiting" },
              { value: "approved", label: "Live" }, { value: "archived", label: "Withdrawn" }, { value: "rejected", label: "Rejected" },
            ]} />
            <div className="cx-row" style={{ minWidth: 220 }}>
              <LuSearch aria-hidden />
              <Input aria-label="Search answers" placeholder="Search answers" value={q} onChange={e => setQ(e.target.value)} />
            </div>
          </div>
          {entries.error && <div className="cc-callout" role="alert">{errText(entries.error)}</div>}
          {entries.loading && !entries.data ? <Skeleton lines={6} /> : rows.length === 0 ? (
            <Card><EmptyState icon={<LuBookOpen />} title={q ? "No answers match" : "No answers here yet"}
              body={section?.required ? "This section is required: the concierge needs at least one approved answer here before it goes live." : "Add the first answer, or import the hotel's old knowledge below."}
              action={manage ? <Button onClick={() => setEditing(null)}>New answer</Button> : undefined} /></Card>
          ) : rows.map(e => (
            <EntryCard key={e.id} entry={e} sections={sections} canManage={manage}
              onEdit={() => setEditing(e)} onApprove={() => void quickApprove(e)} />
          ))}
        </div>
      </div>

      {manage && overview.data && (
        <div id="import"><ImportPanel slug={slug} overview={overview.data} onChanged={refresh} /></div>
      )}

      <EntryEditor slug={slug} open={editing !== undefined} entry={editing ?? null} sections={sections}
        defaultSectionId={sectionId} onClose={() => setEditing(undefined)} onChanged={refresh} />
    </>
  );
}
