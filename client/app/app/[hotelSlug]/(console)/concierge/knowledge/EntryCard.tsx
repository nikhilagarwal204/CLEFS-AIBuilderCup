"use client";

/* One answer in the list: question, English answer, the Arabic answer
   right-to-left beneath it, and its status. A pending answer gets a
   one-tap Approve, since reviewing a batch of imported answers is the
   common case and opening the drawer for each would be slow. */

import { LuPencil } from "react-icons/lu";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button } from "../../../../../ui";
import { KIND_LABEL, STATUS_LABEL, STATUS_TONE } from "../lib";
import type { KbEntry, KbSection } from "../types";

export function EntryCard({ entry: e, sections, canManage, onEdit, onApprove }: {
  entry: KbEntry;
  sections: KbSection[];
  canManage: boolean;
  onEdit: () => void;
  onApprove: () => void;
}) {
  const section = sections.find(s => s.id === e.section_id);
  // Hotel-local date: a seasonal answer switches at the hotel's midnight.
  const today = useFormat().dayKey(new Date());
  const outOfDate = (e.valid_to && e.valid_to < today) || (e.valid_from && e.valid_from > today);
  return (
    <article className="cc-entry">
      {e.question_en && <div className="cc-entry__q">{e.question_en}</div>}
      <div className="cc-entry__a">{e.answer_en}</div>
      {(e.question_ar || e.answer_ar) && (
        <div className="cc-entry__ar" dir="rtl" lang="ar">
          {e.question_ar && <strong>{e.question_ar} </strong>}{e.answer_ar}
        </div>
      )}
      <div className="cc-entry__foot">
        <Badge tone={STATUS_TONE[e.status]} dot>{STATUS_LABEL[e.status]}</Badge>
        <Badge>{KIND_LABEL[e.kind] ?? e.kind}</Badge>
        {section && <span className="cc-small cx-muted">{section.title_en}</span>}
        {e.fact_key && <span className="cc-small cx-muted cc-mono">{e.fact_key}={e.fact_value}</span>}
        {e.location_slug && <span className="cc-small cx-muted">Venue: {e.location_slug}</span>}
        {e.source_url && (
          <a className="cc-small" href={e.source_url} target="_blank" rel="noreferrer noopener">{e.source_label || "Source"}</a>
        )}
        {outOfDate && <Badge tone="neutral">Out of season</Badge>}
        {!e.answer_ar && <span className="cc-small cx-faint">No Arabic yet</span>}
        <span className="cc-grow" />
        {canManage && e.status === "pending" && <Button size="sm" onClick={onApprove}>Approve</Button>}
        <Button size="sm" variant="ghost" icon={<LuPencil />} onClick={onEdit}>{canManage ? "Edit" : "View"}</Button>
      </div>
    </article>
  );
}
