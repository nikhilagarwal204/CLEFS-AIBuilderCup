"use client";

/* The history of one answer: every save, approval, rejection and
   withdrawal, newest first, with who did it. Collapsed by default because
   it is audit detail, not something a manager reads every time. */

import { useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Button, Skeleton } from "../../../../../ui";
import { ccApi, errText } from "../lib";
import type { KbRevision } from "../types";

const CHANGE_LABEL: Record<string, string> = {
  created: "Created", edited: "Edited", approved: "Approved", rejected: "Rejected", archived: "Withdrawn",
};

export function Revisions({ slug, entryId, version }: { slug: string; entryId: number; version: number }) {
  const [open, setOpen] = useState(false);
  const fmt = useFormat();
  const revs = useApi<KbRevision[]>(open ? ccApi(slug, `/kb/entries/${entryId}/revisions`) : null, [entryId, version]);

  if (!open) {
    return <div><Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Show history</Button></div>;
  }
  return (
    <div className="cx-stack" style={{ gap: 8 }}>
      <span className="cc-label">History</span>
      {revs.loading && <Skeleton lines={3} />}
      {revs.error && <p className="cc-sub">{errText(revs.error)}</p>}
      {revs.data?.length === 0 && <p className="cc-sub">No history yet.</p>}
      {revs.data?.map(r => (
        <div key={r.id} className="cc-spread cc-small">
          <span>
            <strong>{CHANGE_LABEL[r.change] ?? r.change}</strong> <span className="cx-muted">v{r.version}</span>
            {r.snapshot?.answer_en ? <span className="cx-muted">: {String(r.snapshot.answer_en).slice(0, 90)}</span> : null}
          </span>
          <span className="cx-muted">{r.actor_type === "user" ? "Staff" : r.actor_type ?? ""} {fmt.dateTime(r.at)}</span>
        </div>
      ))}
    </div>
  );
}
