"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Rate parity (02 spec 3.3 L7, 3.10 parity/): prices seen on each channel
   for the same stay, compared like-for-like the way the L7 check compares
   them (same date, nights, adults, room, device, signed-in state, currency
   and tax basis). A channel priced below the cheapest other channel by
   more than the owner's tolerance is marked; the check turns it into a
   finding sized by the rooms booked on that channel. Screenshots link
   from each price.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import Link from "next/link";
import { API_PREFIX, hotelPath, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Card, Segmented } from "../../../../../ui";
import { isDirect, num, otaLabel, parityGrid, pgApi, pgHref } from "../lib";
import type { RateEvidence } from "../types";
import { DataCard, Money, useCanManage } from "./bits";
import { RateEvidenceForm } from "./RateEvidenceForm";

const LOGIN_TEXT: Record<string, string> = { LOGGED_OUT: "signed out", LOGGED_IN: "signed in", MEMBER: "member" };

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function ParityView() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const canManage = useCanManage();
  const today = fmt.dayKey(new Date());
  const [span, setSpan] = useState<"upcoming" | "past">("upcoming");
  const range = span === "upcoming" ? { from: today, to: addDays(today, 120) } : { from: addDays(today, -60), to: today };
  const q = useApi<RateEvidence[]>(withQuery(pgApi(slug, "/rate-evidence"), range));
  const policies = useApi<{ parity_tolerance_pct: string | number }>(pgApi(slug, "/policies"));
  const tol = num(policies.data?.parity_tolerance_pct) ?? 1;
  const grid = useMemo(() => parityGrid(q.data ?? [], tol), [q.data, tol]);
  const channels = useMemo(() => {
    const all = [...new Set(grid.flatMap(g => Object.keys(g.prices)))];
    return all.sort((a, b) => Number(isDirect(b)) - Number(isDirect(a)) || a.localeCompare(b));
  }, [grid]);
  const breaches = grid.filter(g => g.breaches.length).length;

  return (
    <>
      {canManage && (
        <Card title="Record a price" subtitle="What a guest would see on one channel, with the conditions that make it comparable.">
          <RateEvidenceForm slug={slug} onSaved={() => q.reload()} />
        </Card>
      )}
      <div className="pg-between">
        <Segmented ariaLabel="Stay dates" value={span} onChange={v => setSpan(v as "upcoming" | "past")}
                   options={[{ value: "upcoming", label: "Upcoming stays" }, { value: "past", label: "Last 60 days" }]} />
        <span className="pg-faint">
          Tolerance {fmt.number(tol, 2)}%. {breaches ? `${breaches} comparison${breaches === 1 ? "" : "s"} undercut. ` : ""}
          <Link className="cx-link" href={pgHref(slug, "/findings?detector=L7&status=all")}>Parity findings</Link>
        </span>
      </div>
      <DataCard title="Parity grid" subtitle="Latest price per channel for each like-for-like search. Marked prices undercut the cheapest other channel."
                loading={q.loading} error={q.error} empty={!grid.length} emptyTitle="No prices recorded for these dates"
                emptyBody="Record what you see on your website and each OTA for the same stay.">
        <div className="pg-scroll">
          <table className="pg-grid-table">
            <thead>
              <tr>
                <th scope="col">Search</th>
                {channels.map(c => <th key={c} scope="col">{otaLabel(c)}</th>)}
              </tr>
            </thead>
            <tbody>
              {grid.map(g => (
                <tr key={g.key}>
                  <td>
                    <div className="pg-strong">{fmt.date(g.stay_date, "medium")}</div>
                    <div className="pg-faint">
                      {g.los} night{g.los === 1 ? "" : "s"}, {g.adults} adult{g.adults === 1 ? "" : "s"}{g.room_label ? `, ${g.room_label}` : ""}
                      {` · ${g.device.toLowerCase()}, ${LOGIN_TEXT[g.login_state] ?? g.login_state}`}
                    </div>
                  </td>
                  {channels.map(c => {
                    const r = g.prices[c];
                    if (!r) return <td key={c}><span className="pg-faint">n/a</span></td>;
                    const low = g.breaches.includes(c);
                    return (
                      <td key={c} className={low ? "pg-cell--low" : undefined}>
                        <Money minor={r.price_minor} currency={r.currency} />
                        {low && <> <Badge tone="bad">Undercut</Badge></>}
                        {r.screenshot_file_id && (
                          <div>
                            <a className="cx-link pg-faint" target="_blank" rel="noopener noreferrer"
                               href={`${API_PREFIX}${hotelPath(slug, `/files/${encodeURIComponent(r.screenshot_file_id)}`)}`}>
                              Screenshot
                            </a>
                          </div>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </DataCard>
    </>
  );
}
