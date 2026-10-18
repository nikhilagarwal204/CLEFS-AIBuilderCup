"use client";

/* PilotScorecard (02 spec 3.4): the pilot succeeds on either of two
   criteria: 3 or more valid findings (accepted, disputed or recovered), or
   a valid recoverable amount of at least 3 x the monthly subscription x
   the pilot months. Both are shown with their progress so the owner sees
   how close the pilot is, not only a pass or fail. */

import { LuCheck, LuCircle } from "react-icons/lu";
import { Badge } from "../../../../../ui";
import { STATUS_LABEL } from "../lib";
import type { FindingStatus, PilotScorecard as Scorecard } from "../types";
import { Money } from "./bits";

function Meter({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return <div className="pg-meter" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>;
}

export function PilotScorecard({ card, formatDate }: { card: Scorecard; formatDate: (iso: string) => string }) {
  const statuses = Object.entries(card.by_status ?? {}).filter(([, n]) => (n ?? 0) > 0) as [FindingStatus, number][];
  return (
    <div className="pg-criteria">
      <div className="pg-between">
        <Badge tone={card.success ? "ok" : "gold"} dot>{card.success ? "Pilot target met" : "Pilot in progress"}</Badge>
        <span className="pg-faint">
          {card.pilot_started_on ? `Since ${formatDate(card.pilot_started_on)}` : "All time"}
          {`, ${card.pilot_months} month${card.pilot_months === 1 ? "" : "s"}`}
        </span>
      </div>

      <div className="pg-criterion">
        {card.criteria.count ? <LuCheck aria-label="Met" /> : <LuCircle aria-label="Not yet" />}
        <div>
          <div className="pg-strong">{card.valid_findings} of {card.min_valid} valid findings</div>
          <div className="pg-faint">
            {statuses.length ? statuses.map(([s, n]) => `${n} ${STATUS_LABEL[s].toLowerCase()}`).join(", ") : "None accepted yet"}
          </div>
          <Meter value={card.valid_findings} max={card.min_valid} />
        </div>
      </div>

      <div className="pg-criterion">
        {card.criteria.amount ? <LuCheck aria-label="Met" /> : <LuCircle aria-label="Not yet" />}
        <div>
          <div className="pg-strong">
            <Money minor={card.valid_recoverable_minor} currency={card.currency} /> recoverable
            {card.amount_threshold_minor !== null && (
              <> of <Money minor={card.amount_threshold_minor} currency={card.currency} /></>
            )}
          </div>
          <div className="pg-faint">
            {card.amount_threshold_minor === null
              ? "No subscription price is set for this pilot, so only the count is judged."
              : "Target: 3 x the monthly subscription x the pilot months."}
            {" "}Recovered so far: <Money minor={card.recovered_minor} currency={card.currency} />.
          </div>
          {card.amount_threshold_minor !== null && (
            <Meter value={card.valid_recoverable_minor} max={card.amount_threshold_minor} />
          )}
        </div>
      </div>
    </div>
  );
}
