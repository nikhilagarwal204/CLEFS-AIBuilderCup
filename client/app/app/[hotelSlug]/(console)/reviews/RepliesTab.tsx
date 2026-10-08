"use client";

/* Replies waiting on the team: drafts first, then approved ones still to be
   posted or copied. Copy works right from the list (the fast path for OTA
   reviews that can only be answered by hand); everything else opens the
   review drawer so the reply is always read next to the review. */

import { useState } from "react";
import { apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, EmptyState, Segmented, Skeleton, useToast } from "../../../../ui";
import { REPLY_STATUS, copyText, errText } from "./lib";
import type { Reply } from "./types";

type Filter = "draft,approved" | "draft" | "approved" | "copied,posted";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "draft,approved", label: "To do" },
  { value: "draft", label: "Drafts" },
  { value: "approved", label: "Approved" },
  { value: "copied,posted", label: "Sent" },
];

export default function RepliesTab({ slug, version, onOpen }: {
  slug: string;
  version: number;
  onOpen: (reviewId: number) => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const canManage = can("reviews.manage");
  const [filter, setFilter] = useState<Filter>("draft,approved");
  const [copying, setCopying] = useState<number | null>(null);
  const res = useApi<Reply[]>(hotelPath(slug, `/reviews/replies?status=${filter}&limit=50`), [slug, filter, version]);

  const copy = async (r: Reply) => {
    // Clipboard first, inside the click, before any network await.
    if (!(await copyText(r.draft_text))) { toast.error("Could not copy", "Open the review and copy the text by hand."); return; }
    if (!canManage || r.status === "copied" || r.status === "posted") { toast.success("Copied"); return; }
    setCopying(r.id);
    try {
      await apiFetch<Reply>(hotelPath(slug, `/reviews/replies/${r.id}/copied`), { method: "POST" });
      toast.success("Copied", "Paste it on the platform. The review now counts as answered.");
      res.reload();
    } catch (err) {
      toast.error("Copied, but not recorded", errText(err));
    } finally {
      setCopying(null);
    }
  };

  const rows = res.data ?? [];

  return (
    <Card
      title="Replies"
      subtitle="Drafted in the guest's language, under 120 words, with no admission of fault or public offer."
      actions={<Segmented options={FILTERS} value={filter} onChange={setFilter} size="sm" ariaLabel="Reply status" />}
    >
      {res.loading && !res.data ? (
        <Skeleton lines={4} height={64} gap={10} />
      ) : res.error ? (
        <EmptyState title="Could not load replies" body={errText(res.error)}
          action={<Button onClick={res.reload}>Try again</Button>} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={filter === "copied,posted" ? "Nothing sent yet" : "No replies waiting"}
          body={filter === "copied,posted" ? undefined : "New reviews get a drafted reply within about 15 minutes."}
        />
      ) : (
        <ul className="rev-list" aria-label="Replies">
          {rows.map(r => {
            const st = REPLY_STATUS[r.status];
            return (
              <li key={r.id} className="rev-card" style={{ cursor: "default" }}>
                <div className="rev-card__head">
                  <Badge tone={st.tone} dot>{st.label}</Badge>
                  {/* An approved reply has had its approval (QA OWN-10: both chips showed). */}
                  {r.needs_approval && r.status === "draft" && <Badge tone="warn">Needs approval</Badge>}
                  {r.language && <Badge>{r.language.toUpperCase()}</Badge>}
                  <span className="rev-spacer" />
                  <span className="rev-card__meta">{fmt.dateTime(r.posted_at ?? r.updated_at)}</span>
                </div>
                <div className="rev-card__text" dir="auto">{r.draft_text}</div>
                <div className="rev-actions">
                  <Button size="sm" variant="secondary" loading={copying === r.id} onClick={() => void copy(r)}>Copy</Button>
                  <Button size="sm" variant="ghost" onClick={() => onOpen(r.review_id)}>Open review</Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
