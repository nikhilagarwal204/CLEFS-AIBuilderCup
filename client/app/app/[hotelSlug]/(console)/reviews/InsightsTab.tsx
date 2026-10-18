"use client";

/* What guests keep mentioning, and whether it is getting worse. Themes come
   from a fixed taxonomy mapped to Service departments, so "Housekeeping:
   cleanliness" here is the same bucket the weekly trend signal watches.
   Weeks are ISO Mondays bucketed in hotel time by the server. */

import { useMemo, useState } from "react";
import { hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { ChartCard, HBarChart, CHART_DONE, CHART_QUIET } from "../../../charts";
import { Button, EmptyState, LineChart, Segmented, Skeleton, type LineSeries } from "../../../../ui";
import { errText } from "./lib";
import type { ThemeRow, Trends } from "./types";

const NEG = "#D9534F";
const TOP_THEMES = 5;

const PERIODS = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "1 year" },
] as const;

export default function InsightsTab({ slug }: { slug: string }) {
  const fmt = useFormat();
  const [days, setDays] = useState<number>(90);
  const themes = useApi<ThemeRow[]>(hotelPath(slug, `/reviews/themes?days=${days}`), [slug, days]);
  const trends = useApi<Trends>(hotelPath(slug, "/reviews/trends?weeks=12"), [slug]);

  // ─── Theme bars ───
  const bars = useMemo(() => (themes.data ?? [])
    .filter(t => t.negative + t.positive + t.neutral > 0)
    .slice(0, 12)
    .map(t => ({
      label: t.label,
      values: [t.negative, t.positive, t.neutral],
      note: t.department ?? undefined,
    })), [themes.data]);

  // ─── Trend lines ───
  // The themes with the most negative mentions over the window, so the chart
  // shows what is actually hurting rather than 20 flat lines.
  const { ratingSeries, negSeries } = useMemo(() => {
    const t = trends.data;
    if (!t) return { ratingSeries: [] as LineSeries[], negSeries: [] as LineSeries[] };
    const rating: LineSeries = {
      key: "rating", label: "Average rating", color: CHART_DONE,
      points: t.weeks.map((w, i) => ({ x: w, y: t.rating[i]?.avg_rating ?? null })),
    };
    const ranked = Object.entries(t.themes)
      .map(([key, v]) => ({ key, v, total: v.negative.reduce((a, b) => a + b, 0) }))
      .filter(x => x.total > 0)
      .sort((a, b) => b.total - a.total)
      .slice(0, TOP_THEMES);
    const neg: LineSeries[] = ranked.map(({ key, v }) => ({
      key, label: v.label,
      points: t.weeks.map((w, i) => ({ x: w, y: v.negative[i] ?? 0 })),
    }));
    return { ratingSeries: [rating], negSeries: neg };
  }, [trends.data]);

  const weeklyReviews = trends.data?.rating.reduce((a, r) => a + r.reviews, 0) ?? 0;

  return (
    <div className="rev-grid2">
      <ChartCard
        title="Themes"
        hint="Mentions per theme, most complained-about first. The note is the department it maps to."
        legend={[{ label: "Negative", color: NEG }, { label: "Positive", color: CHART_DONE }, { label: "Neutral", color: CHART_QUIET }]}
        aside={<Segmented options={PERIODS} value={days} onChange={setDays} size="sm" ariaLabel="Theme period" />}
      >
        {themes.loading && !themes.data ? (
          <Skeleton lines={6} />
        ) : themes.error ? (
          <EmptyState title="Could not load themes" body={errText(themes.error)}
            action={<Button onClick={themes.reload}>Try again</Button>} />
        ) : (
          <HBarChart
            rows={bars}
            series={[{ label: "Negative", color: NEG }, { label: "Positive", color: CHART_DONE }, { label: "Neutral", color: CHART_QUIET }]}
            unit=" mentions"
            ariaLabel="Review themes by polarity"
            emptyNote="No analysed reviews in this period yet."
          />
        )}
      </ChartCard>

      <div className="cx-stack">
        <ChartCard title="Rating by week" hint={`Average on a 5-point scale over the last 12 weeks (${fmt.number(weeklyReviews)} reviews).`}>
          {trends.loading && !trends.data ? (
            <Skeleton height={200} />
          ) : trends.error ? (
            <EmptyState title="Could not load trends" body={errText(trends.error)}
              action={<Button onClick={trends.reload}>Try again</Button>} />
          ) : (
            <LineChart series={ratingSeries} height={200} ariaLabel="Average rating per week"
              yFormat={v => v.toFixed(1)} emptyNote="No rated reviews in the last 12 weeks." />
          )}
        </ChartCard>
        <ChartCard title="Complaints by week" hint="Negative mentions of the top themes. A jump of 50% or more is flagged in the Brief.">
          {trends.loading && !trends.data ? (
            <Skeleton height={200} />
          ) : trends.error ? null : (
            <LineChart series={negSeries} height={200} ariaLabel="Negative theme mentions per week"
              yFormat={v => String(Math.round(v))} emptyNote="No complaints in the last 12 weeks." />
          )}
        </ChartCard>
      </div>
    </div>
  );
}
