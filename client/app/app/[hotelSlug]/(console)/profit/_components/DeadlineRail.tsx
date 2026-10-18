"use client";

/* DeadlineRail: everything with a clock on it, soonest first. OTA dispute
   windows are short (Booking no-shows 48 h after check-out, invoices 14
   business days), and a missed window turns a recoverable finding into a
   lost one, so the Overview leads with these. Each finding links to its
   detail; a Tourism Dirham month links to the tax page. */

import Link from "next/link";
import { CountdownChip } from "../../../../../ui";
import { detectorLabel, monthLabel, otaLabel, pgHref, sortDeadlines } from "../lib";
import type { Deadline } from "../types";
import { Money } from "./bits";

export function DeadlineRail({ slug, items, limit = 8 }: { slug: string; items: readonly Deadline[]; limit?: number }) {
  const rows = sortDeadlines(items).slice(0, limit);
  if (!rows.length) {
    return <div className="chart-empty" style={{ height: 80 }}>No deadlines in the next two weeks.</div>;
  }
  return (
    <ul className="pg-rail">
      {rows.map(d => {
        if (d.kind === "td_remittance") {
          return (
            <li key={`td-${d.month}`} className="pg-rail__item">
              <Link href={pgHref(slug, "/tax")}>
                <div className="pg-rail__title">Tourism Dirham remittance, {monthLabel(d.month)}</div>
              </Link>
              <CountdownChip due={d.deadline_at} warnWithinHours={120} />
              <div className="pg-rail__meta"><Money minor={d.amount_minor} currency={d.currency} /> to remit to DET</div>
            </li>
          );
        }
        return (
          <li key={`f-${d.id}`} className="pg-rail__item">
            <Link href={pgHref(slug, `/findings/${d.id}`)}>
              <div className="pg-rail__title">{detectorLabel(d.detector)}</div>
            </Link>
            <CountdownChip due={d.deadline_at} warnWithinHours={72} />
            <div className="pg-rail__meta">
              {otaLabel(d.ota_code)}{d.booking_ref ? `, booking ${d.booking_ref}` : ""}{" · "}
              <Money minor={d.amount_minor} currency={d.currency} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
