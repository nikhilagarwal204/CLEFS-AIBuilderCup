"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Top 5 this week (02 spec 4.3): the open rate suggestions in the next 60
   nights ranked by estimated opportunity x confidence, at most two per
   driving event. Each one can be approved, adjusted or rejected in place.
   The same list raises the Monday dr.opportunity signals in the Brief.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, EmptyState, Skeleton } from "../../../../../ui";
import DayDrawer from "../_components/DayDrawer";
import RecommendationCard from "../_components/RecommendationCard";
import { addDays, rankValue } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { DemandEvent, Top5Response } from "../_lib/types";

export default function Top5Page() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [today] = useState(() => fmt.dayKey(Date.now()));
  const [night, setNight] = useState<string | null>(null);
  const top = useApi<Top5Response>(drApi(slug, "/top5"), [slug]);
  const evs = useApi<DemandEvent[]>(withQuery(drApi(slug, "/events"), { from: today, to: addDays(today, 61) }), [slug, today]);
  const titles = useMemo(() => Object.fromEntries((evs.data ?? []).map(e => [e.id, e.title])), [evs.data]);
  const items = top.data?.items ?? [];

  return (
    <>
      <Card
        title="Top 5 this week"
        subtitle={`${top.data?.label ?? "Estimated opportunity x confidence; at most 2 per event"}. Estimates, not promises: outcomes are measured after each night.`}
      >
        {top.error ? (
          <EmptyState title="Could not load the top 5" body={errorText(top.error)} action={<Button onClick={top.reload}>Try again</Button>} />
        ) : top.loading && !top.data ? (
          <Skeleton lines={5} height={22} />
        ) : items.length === 0 ? (
          <EmptyState title="Nothing to decide" body="No open rate suggestions in the next 60 nights. The engine runs every night after the on-the-books snapshot." />
        ) : (
          <ol className="dr-list dr-top">
            {items.map(r => (
              <li key={r.id} className="cx-row" style={{ alignItems: "flex-start", gap: 12, flexWrap: "nowrap" }}>
                <span className="dr-top__n" aria-hidden="true" />
                <div style={{ flex: 1, minWidth: 0 }} className="cx-stack">
                  <div className="cx-row" style={{ justifyContent: "space-between" }}>
                    <b>{fmt.date(r.stay_date, "long")}</b>
                    <span className="cx-row" style={{ gap: 8 }}>
                      {rankValue(r) !== null && (
                        <span className="dr-small dr-muted" title="Estimated opportunity x confidence">
                          Weighted {fmt.money(rankValue(r), { maximumFractionDigits: 0, currency: r.currency })}
                        </span>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => setNight(r.stay_date)}>Open night</Button>
                    </span>
                  </div>
                  <RecommendationCard slug={slug} rec={r} eventTitles={titles} compact onDecided={top.reload} />
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
      <DayDrawer date={night} onClose={() => setNight(null)} onChanged={top.reload} />
    </>
  );
}
