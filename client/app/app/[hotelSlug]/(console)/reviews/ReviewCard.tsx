"use client";

/* One review in the feed: who, where, score, the first lines, themes and
   where its reply stands. The whole card is a button that opens the drawer,
   so it is one tab stop and works with Enter and Space. */

import { useFormat } from "../../../../lib/format";
import { Badge, cx } from "../../../../ui";
import { REPLY_STATUS, isUnanswered, platformLabel, ratingText, severityTone, starCount } from "./lib";
import type { Review } from "./types";

export function Stars({ n, label }: { n: number; label: string }) {
  return (
    <span className="rev-stars" role="img" aria-label={label}>
      {[1, 2, 3, 4, 5].map(i => (
        <span key={i} aria-hidden="true" className={i > n ? "rev-stars__off" : undefined}>★</span>
      ))}
    </span>
  );
}

export default function ReviewCard({ review: r, onOpen }: { review: Review; onOpen: (id: number) => void }) {
  const fmt = useFormat();
  const when = r.posted_at ?? r.ingested_at;
  const status = r.reply_status ? REPLY_STATUS[r.reply_status] : null;

  return (
    <li>
      <button type="button" className={cx("rev-card", r.negative && "rev-card--neg")} onClick={() => onOpen(r.id)}>
        <span className="rev-card__head">
          <Stars n={starCount(r)} label={ratingText(r)} />
          <span className="rev-card__who">{r.author_display || "Guest"}</span>
          <Badge>{platformLabel(r.platform)}</Badge>
          {r.severity && r.severity !== "none" && (
            <Badge tone={severityTone(r.severity)}>{r.severity} severity</Badge>
          )}
          <span className="rev-spacer" />
          <span className="rev-card__meta">{fmt.date(when, "medium")}</span>
        </span>
        {r.title && <span className="rev-card__title">{r.title}</span>}
        {r.purged ? (
          <span className="rev-muted">The review text was removed after 30 days. Its score and themes are kept.</span>
        ) : (
          r.text && <span className="rev-card__text">{r.text}</span>
        )}
        <span className="rev-card__head">
          <span className="rev-tags">
            {r.themes.slice(0, 4).map(t => (
              <Badge key={t.key} tone={t.polarity === "negative" ? "warn" : t.polarity === "positive" ? "ok" : "neutral"}>
                {t.label}
              </Badge>
            ))}
            {r.room_hint && <Badge tone="info">Room {r.room_hint}</Badge>}
            {r.linked_request_ids.length > 0 && (
              <Badge tone="info">{r.linked_request_ids.length} linked request{r.linked_request_ids.length > 1 ? "s" : ""}</Badge>
            )}
          </span>
          <span className="rev-spacer" />
          {r.has_owner_response ? (
            <Badge tone="ok">Answered</Badge>
          ) : status ? (
            <Badge tone={status.tone} dot>{status.label} reply</Badge>
          ) : isUnanswered(r) ? (
            <Badge tone="neutral">No reply yet</Badge>
          ) : null}
        </span>
      </button>
    </li>
  );
}
