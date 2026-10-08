"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Returning arrivals for one hotel-local day. The day defaults to today in
   the HOTEL's timezone (not the browser's), so a GM abroad sees the same
   list as the front desk. Badges are masked server-side per role.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Field, Input } from "../../../../../ui";
import BadgeList from "../BadgeList";
import { gstApi } from "../lib";
import type { Badge } from "../types";

function shiftDay(day: string, by: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
}

export default function ArrivalsPage() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const today = fmt.dayKey(new Date());
  const [day, setDay] = useState(today);
  const res = useApi<{ day: string; items: Badge[] }>(withQuery(gstApi(slug, "/arrivals"), { day }), [slug, day]);

  return (
    <Card
      title="Returning arrivals"
      subtitle="Guests who agreed to be remembered and have stayed before. VIP arrivals also raise a signal in the brief."
      actions={
        <div className="gst-toolbar">
          <Button size="sm" variant="ghost" onClick={() => setDay(shiftDay(day, -1))} aria-label="Previous day">Prev</Button>
          <Field label="Day" htmlFor="gst-arr-day">
            <Input id="gst-arr-day" type="date" value={day} onChange={e => e.target.value && setDay(e.target.value)} />
          </Field>
          <Button size="sm" variant="ghost" onClick={() => setDay(shiftDay(day, 1))} aria-label="Next day">Next</Button>
          {day !== today && <Button size="sm" variant="secondary" onClick={() => setDay(today)}>Today</Button>}
        </div>
      }
    >
      <BadgeList
        items={res.data?.items}
        loading={res.loading && !res.data}
        error={res.error?.message}
        empty={`No returning guests arrive on ${fmt.date(`${day}T12:00:00Z`, "medium")}.`}
      />
    </Card>
  );
}
