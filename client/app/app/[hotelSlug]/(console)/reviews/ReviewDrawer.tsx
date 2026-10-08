"use client";

/* One review in full: the text, what the analysis found (themes, severity,
   room, stay date), the Service requests it links to, and the reply. Opened
   from the feed or the replies list; the parent owns which review is open. */

import { useState } from "react";
import { apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, Drawer, EmptyState, Skeleton, useToast } from "../../../../ui";
import { Stars } from "./ReviewCard";
import ReplyEditor from "./ReplyEditor";
import { errText, platformLabel, ratingText, severityTone, starCount } from "./lib";
import type { ReviewDetail } from "./types";

export default function ReviewDrawer({ slug, reviewId, canManage, onClose, onChanged }: {
  slug: string;
  reviewId: number | null;
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const res = useApi<ReviewDetail>(reviewId === null ? null : hotelPath(slug, `/reviews/${reviewId}`), [slug, reviewId]);
  const [analysing, setAnalysing] = useState(false);
  const r = res.data && res.data.id === reviewId ? res.data : undefined;

  // A reply change refreshes this review and tells the console (tiles, list).
  const changed = () => { res.reload(); onChanged(); };

  const reanalyse = async () => {
    if (!r) return;
    setAnalysing(true);
    try {
      await apiFetch(hotelPath(slug, `/reviews/${r.id}/analyze`), { method: "POST" });
      toast.success("Analysis refreshed");
      changed();
    } catch (err) {
      toast.error("Could not re-analyse", errText(err));
    } finally {
      setAnalysing(false);
    }
  };

  return (
    <Drawer open={reviewId !== null} onClose={onClose} wide
      title={r ? `${platformLabel(r.platform)} review` : "Review"}>
      {!r && res.error ? (
        <EmptyState title="Could not load this review" body={errText(res.error)}
          action={<Button onClick={res.reload}>Try again</Button>} />
      ) : !r ? (
        <Skeleton lines={6} />
      ) : (
        <ReviewBody review={r} canManage={canManage} slug={slug} onChanged={changed}
          onReanalyse={reanalyse} analysing={analysing} />
      )}
    </Drawer>
  );
}

// ─── Body ────────────────────────────────────────────────────────────────

function ReviewBody({ review: r, slug, canManage, onChanged, onReanalyse, analysing }: {
  review: ReviewDetail;
  slug: string;
  canManage: boolean;
  onChanged: () => void;
  onReanalyse: () => void;
  analysing: boolean;
}) {
  const fmt = useFormat();
  return (
    <div>
      <div className="rev-card__head">
        <Stars n={starCount(r)} label={ratingText(r)} />
        <strong>{ratingText(r)}</strong>
        <span className="rev-card__who">{r.author_display || "Guest"}</span>
        <Badge>{platformLabel(r.platform)}</Badge>
        {r.negative && <Badge tone="bad">Negative</Badge>}
        {r.severity && r.severity !== "none" && <Badge tone={severityTone(r.severity)}>{r.severity} severity</Badge>}
      </div>
      <p className="rev-muted" style={{ margin: "6px 0 12px" }}>
        Posted {fmt.dateTime(r.posted_at ?? r.ingested_at, "medium")}
        {r.source_label ? ` · via ${r.source_label}` : ""}
      </p>
      {r.title && <div className="rev-card__title" dir="auto">{r.title}</div>}
      {r.purged ? (
        <p className="rev-muted">The review text was removed after 30 days (privacy rule). The score and themes stay.</p>
      ) : (
        <div className="rev-body" dir="auto">{r.text || "No written comment."}</div>
      )}

      <section className="rev-section" aria-label="What the analysis found">
        <h3>What it is about</h3>
        {r.summary_en && <p style={{ margin: 0 }}>{r.summary_en}</p>}
        {r.themes.length > 0 ? (
          <ul className="rev-list">
            {r.themes.map(t => (
              <li key={t.key} className="rev-row">
                <span>
                  <Badge tone={t.polarity === "negative" ? "warn" : t.polarity === "positive" ? "ok" : "neutral"}>{t.label}</Badge>
                  {t.summary_en && <span className="rev-muted"> {t.summary_en}</span>}
                </span>
                {t.department && <span className="rev-muted">{t.department}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="rev-muted">{r.analyzed_at ? "No themes found." : "Not analysed yet. It runs every 15 minutes."}</p>
        )}
        <dl className="rev-kv">
          {r.room_hint && (<><dt>Room</dt><dd>{r.room_hint}</dd></>)}
          {r.stay_date_guess && (<><dt>Stay around</dt><dd>{fmt.date(r.stay_date_guess, "medium")}</dd></>)}
          {r.language && (<><dt>Language</dt><dd>{r.language}</dd></>)}
        </dl>
        {canManage && (
          <div className="rev-actions">
            <Button variant="ghost" size="sm" loading={analysing} onClick={onReanalyse}>Re-analyse</Button>
          </div>
        )}
      </section>

      {r.linked_requests.length > 0 && (
        <section className="rev-section" aria-label="Linked service requests">
          <h3>Requests during the stay</h3>
          <ul className="rev-list">
            {r.linked_requests.map(q => (
              <li key={q.id} className="rev-row">
                <span>
                  <a href={`/app/${encodeURIComponent(slug)}/service`}>#{q.id}</a>
                  {" "}{q.request_type ?? "Request"}{q.room ? `, room ${q.room}` : ""}
                </span>
                <span className="rev-muted">
                  {q.status}{q.resolved_at ? ` · resolved ${fmt.dateTime(q.resolved_at)}` : ` · opened ${fmt.dateTime(q.created_at)}`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rev-section" aria-label="Reply">
        <h3>Reply</h3>
        <ReplyEditor slug={slug} review={r} canManage={canManage} onChanged={onChanged} />
      </section>
    </div>
  );
}
