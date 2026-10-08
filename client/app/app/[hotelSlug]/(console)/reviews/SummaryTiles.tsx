"use client";

/* The last 30 days at a glance. Percentages arrive as 0-100 from the server
   (queries.summary), so they are formatted without the ratio flag. */

import type { UseApiResult } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { StatTile } from "../../../../ui";
import { platformLabel } from "./lib";
import type { Summary } from "./types";

export default function SummaryTiles({ summary }: { summary: UseApiResult<Summary> }) {
  const fmt = useFormat();
  const s = summary.data;
  const loading = summary.loading && !s;

  // The busiest platform, so the rating tile says where the number comes from.
  const top = s
    ? Object.entries(s.by_platform).sort((a, b) => b[1].count - a[1].count)[0]
    : undefined;

  if (summary.error && !s) {
    return <p className="rev-muted" role="status">Could not load the 30-day summary: {summary.error.message}</p>;
  }

  return (
    <div className="rev-tiles">
      <StatTile
        label="Reviews, 30 days"
        value={s ? fmt.number(s.count) : "-"}
        sub={top ? `${platformLabel(top[0])}: ${fmt.number(top[1].count)}` : undefined}
        loading={loading}
      />
      <StatTile
        label="Average rating"
        value={s?.avg_rating != null ? `${s.avg_rating.toFixed(2)} / 5` : "-"}
        sub="All platforms, on a 5-point scale"
        loading={loading}
      />
      <StatTile
        label="Answered"
        value={s ? fmt.pct(s.response_rate) : "-"}
        sub={s ? `${fmt.number(s.unanswered)} still waiting` : undefined}
        loading={loading}
      />
      <StatTile
        label="Negative"
        value={s ? fmt.pct(s.negative_share) : "-"}
        sub="Share of analysed reviews"
        loading={loading}
      />
      <StatTile
        label="Drafts to review"
        value={s ? fmt.number(s.drafts_waiting) : "-"}
        sub="Replies written and waiting for you"
        loading={loading}
      />
    </div>
  );
}
