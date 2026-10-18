"use client";

/* The review feed, newest first, with filters and keyset paging (before_id)
   so "Load more" never skips or repeats a review that arrived meanwhile.
   Pages are fetched by hand rather than through useApi because the list
   accumulates across requests. */

import { useCallback, useRef, useState } from "react";
import { apiFetch, hotelPath, useApi, withQuery } from "../../../../lib/api";
import { Button, Card, EmptyState, Field, Select, Skeleton } from "../../../../ui";
import ReviewCard from "./ReviewCard";
import { errText } from "./lib";
import type { Review, ThemeRow } from "./types";

const PAGE = 30;

const PLATFORM_OPTIONS = [
  { value: "", label: "All platforms" },
  { value: "google", label: "Google" },
  { value: "tripadvisor", label: "Tripadvisor" },
  { value: "booking", label: "Booking.com" },
  { value: "agoda", label: "Agoda" },
  { value: "expedia", label: "Expedia" },
  { value: "email", label: "Email" },
];

const SHOW_OPTIONS = [
  { value: "all", label: "All reviews" },
  { value: "unanswered", label: "Not answered" },
  { value: "negative", label: "Negative only" },
  { value: "low", label: "3 stars or less" },
];

const DAY_OPTIONS = [
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "365", label: "Last year" },
];

export default function FeedTab({ slug, version, onOpen }: {
  slug: string;
  version: number;
  onOpen: (id: number) => void;
}) {
  const [platform, setPlatform] = useState("");
  const [show, setShow] = useState("all");
  const [theme, setTheme] = useState("");
  const [days, setDays] = useState("90");
  const themes = useApi<ThemeRow[]>(hotelPath(slug, `/reviews/themes?days=${days}`), [slug, days]);

  // The first page is keyed by the filters and `version`, so any filter
  // change or reply change refetches from the top through useApi.
  const firstPath = withQuery(hotelPath(slug, "/reviews"), {
    platform: platform || undefined,
    negative: show === "negative" ? true : undefined,
    unanswered: show === "unanswered" ? true : undefined,
    max_rating: show === "low" ? 3 : undefined,
    theme: theme || undefined,
    days, limit: PAGE,
  });
  const first = useApi<Review[]>(firstPath, [firstPath, version]);
  const pageKey = `${firstPath}#${version}`;

  // Later pages are only valid for the key they were fetched under; a filter
  // change makes them stale without an effect having to clear them.
  const [extra, setExtra] = useState<{ key: string; rows: Review[]; more: boolean } | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const seq = useRef(0);

  const firstRows = first.data ?? [];
  const extraRows = extra?.key === pageKey ? extra.rows : [];
  const rows = [...firstRows, ...extraRows];
  const hasMore = extra?.key === pageKey ? extra.more : firstRows.length === PAGE;

  const loadMore = useCallback(async (beforeId: number, soFar: Review[]) => {
    const my = ++seq.current;
    setMoreBusy(true);
    setMoreError(null);
    try {
      const page = await apiFetch<Review[]>(withQuery(firstPath, { before_id: beforeId }));
      if (my !== seq.current) return;
      setExtra({ key: pageKey, rows: [...soFar, ...page], more: page.length === PAGE });
    } catch (err) {
      if (my === seq.current) setMoreError(errText(err));
    } finally {
      if (my === seq.current) setMoreBusy(false);
    }
  }, [firstPath, pageKey]);

  const loading = first.loading && !first.data;
  const error = first.error ? errText(first.error) : null;

  const themeOptions = [
    { value: "", label: "Any theme" },
    ...(themes.data ?? []).map(t => ({ value: t.key, label: t.label })),
  ];

  return (
    <Card>
      <div className="rev-toolbar">
        <Field label="Platform">
          <Select value={platform} onChange={e => setPlatform(e.target.value)} options={PLATFORM_OPTIONS} />
        </Field>
        <Field label="Show">
          <Select value={show} onChange={e => setShow(e.target.value)} options={SHOW_OPTIONS} />
        </Field>
        <Field label="Theme">
          <Select value={theme} onChange={e => setTheme(e.target.value)} options={themeOptions} />
        </Field>
        <Field label="Period">
          <Select value={days} onChange={e => setDays(e.target.value)} options={DAY_OPTIONS} />
        </Field>
      </div>

      {loading ? (
        <Skeleton lines={4} height={72} gap={10} />
      ) : error ? (
        <EmptyState title="Could not load reviews" body={error}
          action={<Button onClick={first.reload}>Try again</Button>} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No reviews match"
          body={platform || show !== "all" || theme
            ? "Try a wider filter or a longer period."
            : "Connect a source on the Sources tab, or import a CSV from Booking.com, Agoda or Expedia."}
        />
      ) : (
        <>
          <ul className="rev-list" aria-label="Reviews">
            {rows.map(r => <ReviewCard key={r.id} review={r} onOpen={onOpen} />)}
          </ul>
          {hasMore && (
            <div style={{ marginTop: 14, textAlign: "center" }}>
              {moreError && <p className="rev-muted" role="alert">{moreError}</p>}
              <Button variant="secondary" loading={moreBusy}
                onClick={() => void loadMore(rows[rows.length - 1].id, extraRows)}>
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
