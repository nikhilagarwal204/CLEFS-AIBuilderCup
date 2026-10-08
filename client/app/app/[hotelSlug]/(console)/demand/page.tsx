"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Demand calendar (02 spec 4.4): the 30/60/90-night heatmap coloured by
   the demand index with event badges, and the day drawer for any night.
   Recompute is rate-limited server side (6 an hour).
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useState } from "react";
import { ApiError, api, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Button, Card, EmptyState, Segmented, Skeleton, StatTile, useToast } from "../../../../ui";
import DayDrawer from "./_components/DayDrawer";
import Heatmap from "./_components/Heatmap";
import { drApi, drHref, errorText, PERM } from "./_lib/paths";
import type { CalendarResponse } from "./_lib/types";

type Range = 30 | 60 | 90;

export default function DemandCalendarPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const [range, setRange] = useState<Range>(30);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cal = useApi<CalendarResponse>(can(PERM.view) ? `${drApi(slug, "/calendar")}?days=${range}` : null, [slug, range]);

  const recompute = async () => {
    setBusy(true);
    try {
      await api.post(drApi(slug, "/recompute"));
      toast.success("Demand recomputed", "Scores, the index and rate suggestions are up to date.");
      cal.reload();
    } catch (err) {
      toast.error("Could not recompute", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const cells = cal.data?.cells ?? [];
  const peak = cells.filter(c => (c.demand_index ?? 0) >= 80).length;
  const proposed = cells.filter(c => c.recommendation?.status === "PROPOSED").length;
  const soldOut = cells.filter(c => (c.comp?.sold_out ?? 0) > 0).length;
  const withEvents = cells.filter(c => c.events.length > 0).length;
  const noData = cells.length > 0 && cells.every(c => c.demand_index === null);

  return (
    <>
      <div className="cx-grid cx-grid--4">
        <StatTile label="Peak nights (index 80+)" value={peak} loading={cal.loading && !cal.data} sub={`of ${range}`} />
        <StatTile label="Rate suggestions to decide" value={proposed} loading={cal.loading && !cal.data}
                  sub={proposed ? "Open a night to decide" : "Nothing waiting"} />
        <StatTile label="Nights with events" value={withEvents} loading={cal.loading && !cal.data} />
        <StatTile label="Nights with sold-out competitors" value={soldOut} loading={cal.loading && !cal.data}
                  sub={cal.data?.comp_label ?? "Indicative"} />
      </div>

      <Card
        title="Demand by night"
        subtitle={cal.data
          ? `${fmt.date(cal.data.from, "medium")} to ${fmt.date(cal.data.to, "medium")}. Tap a night for events, competitor rates, pace and the rate suggestion.`
          : "Tap a night for events, competitor rates, pace and the rate suggestion."}
        actions={
          <>
            <Segmented<Range>
              size="sm"
              ariaLabel="Nights shown"
              value={range}
              onChange={setRange}
              options={[{ value: 30, label: "30" }, { value: 60, label: "60" }, { value: 90, label: "90" }]}
            />
            {can(PERM.manage) && (
              <Button size="sm" variant="ghost" loading={busy} onClick={recompute}>Recompute</Button>
            )}
          </>
        }
      >
        {cal.error ? (
          <EmptyState title="Could not load the calendar" body={cal.error instanceof ApiError ? errorText(cal.error) : undefined}
                      action={<Button onClick={cal.reload}>Try again</Button>} />
        ) : cal.loading && !cal.data ? (
          <Skeleton height={320} radius={12} />
        ) : (
          <>
            {noData && (
              <p className="dr-note" style={{ marginBottom: 10 }}>
                No demand inputs yet. Add a BAR and a comp set in <Link className="cx-link" href={drHref(slug, "/settings")}>Settings</Link>,
                or add the events you know about under <Link className="cx-link" href={drHref(slug, "/events")}>Events</Link>.
              </p>
            )}
            <Heatmap cells={cells} selected={selected} onSelect={setSelected} />
          </>
        )}
      </Card>

      <DayDrawer
        date={selected}
        onClose={() => setSelected(null)}
        onChanged={cal.reload}
      />
    </>
  );
}
