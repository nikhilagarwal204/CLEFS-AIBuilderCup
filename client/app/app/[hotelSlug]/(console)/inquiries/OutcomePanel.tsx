"use client";

/* The outcome (4.9): did this inquiry become a verified direct booking, and
   what did it earn net of payment fees? Or did it leak to an OTA? Leaks are
   internal only (Canon 19): the server hides their details without
   guests.view, and nothing here messages the guest. Borderline matches
   (the review band) wait for a person with inquiries.approve. Only a
   "caused" outcome counts toward verified profit; "influenced" is shown
   but labelled as not counted. */

import { useState } from "react";
import { api, useApi, withQuery } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, MoneyText, Skeleton, useToast } from "../../../../ui";
import { errorText, inqPath } from "./lib";
import type { InquiryDetail, MatchReview, Outcome } from "./types";

const OUTCOME_TEXT: Record<string, string> = {
  verified_direct: "Verified direct booking",
  leaked_to_ota: "Booked through an OTA instead",
  lost: "Lost",
  turned_down: "Turned down",
  cancelled_after_confirm: "Cancelled after confirming",
};

export default function OutcomePanel({ row, onChanged }: { row: InquiryDetail; onChanged: () => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const [version, setVersion] = useState(0);
  const outcome = useApi<Outcome>(inqPath(slug, `/${row.id}/outcome`), [version]);
  const canApprove = can("inquiries.approve");
  const reviews = useApi<MatchReview[]>(
    canApprove ? withQuery(inqPath(slug, "/match-reviews"), { status: "open" }) : null, [version]);
  const mine = (reviews.data ?? []).filter(r => r.inquiry_id === row.id);
  const refresh = () => { setVersion(v => v + 1); onChanged(); };

  if (outcome.loading && !outcome.data) return <Skeleton lines={3} height={16} />;
  const o = outcome.data;
  const c = o?.currency ?? row.currency;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {outcome.error && <p className="inq-note inq-note--bad">{outcome.error.message}</p>}
      {o && !o.outcome && (
        <p className="inq-muted" style={{ margin: 0 }}>
          No outcome yet. Clefs checks the PMS every hour for a matching reservation from this guest.
        </p>
      )}
      {o?.outcome && (
        <section className="inq-section">
          <div className="inq-row">
            <Badge tone={o.outcome === "verified_direct" ? "ok" : o.outcome === "leaked_to_ota" ? "bad" : "neutral"}>
              {OUTCOME_TEXT[o.outcome] ?? o.outcome}
            </Badge>
            {o.attribution && (
              <Badge tone={o.attribution === "caused" ? "gold" : "neutral"}
                title={o.attribution === "caused" ? "Clefs sent the reply or quote that led to it" : "The guest booked, but Clefs sent nothing that led to it"}>
                {o.attribution === "caused" ? "Counted" : "Influenced, not counted"}
              </Badge>
            )}
          </div>
          <dl className="inq-kv">
            {o.verified_revenue_value && <><dt>Revenue</dt><dd><MoneyText amount={o.verified_revenue_value} currency={c} /></dd></>}
            {o.commission_avoided_value && <><dt>Commission avoided</dt><dd><MoneyText amount={o.commission_avoided_value} currency={c} /></dd></>}
            {o.payment_fee_value && <><dt>Payment fees</dt><dd><MoneyText amount={o.payment_fee_value} currency={c} /></dd></>}
            {o.outcome_net_value && <><dt>Net gain</dt><dd><MoneyText amount={o.outcome_net_value} currency={c} signed tone="auto" /></dd></>}
            {o.leaked_channel && <><dt>Channel</dt><dd>{o.leaked_channel}</dd></>}
            {o.leaked_commission_value && <><dt>Commission paid</dt><dd><MoneyText amount={o.leaked_commission_value} currency={c} /></dd></>}
            {o.decided_at && <><dt>Decided</dt><dd>{fmt.dateTime(o.decided_at, "medium")}</dd></>}
          </dl>
        </section>
      )}
      {canApprove && mine.length > 0 && (
        <section className="inq-section">
          <h3>Is this the same booking?</h3>
          {mine.map(r => <ReviewRow key={r.id} review={r} onDecided={refresh} />)}
        </section>
      )}
    </div>
  );
}

function ReviewRow({ review, onDecided }: { review: MatchReview; onDecided: () => void }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const [busy, setBusy] = useState<"confirm" | "reject" | null>(null);

  async function decide(what: "confirm" | "reject") {
    setBusy(what);
    try {
      await api.post(inqPath(slug, `/match-reviews/${review.id}/${what}`));
      toast.success(what === "confirm" ? "Match confirmed" : "Not a match", what === "reject" ? "It will not be proposed again." : undefined);
      onDecided();
    } catch (err) {
      toast.error("Not saved", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  const day = (d: string | null) => (d ? fmt.date(`${d}T12:00:00Z`) : "?");
  return (
    <div className="inq-note" style={{ display: "grid", gap: 8 }}>
      <span>
        <Badge tone={review.kind === "leak" ? "warn" : "info"}>{review.kind === "leak" ? "Possible OTA booking" : "Possible direct booking"}</Badge>{" "}
        {review.res_guest_name ?? "A guest"}, {day(review.res_check_in)} to {day(review.res_check_out)}
        {review.channel_code ? ` via ${review.channel_code}` : ""}
        {review.pms_res_id ? ` (PMS ${review.pms_res_id})` : ""}
        <span className="inq-sub">Match score {Math.round(Number(review.score) * 100)}%</span>
      </span>
      <div className="inq-row">
        <Button size="sm" variant="primary" loading={busy === "confirm"} disabled={busy !== null} onClick={() => decide("confirm")}>Same booking</Button>
        <Button size="sm" variant="ghost" loading={busy === "reject"} disabled={busy !== null} onClick={() => decide("reject")}>Not the same</Button>
      </div>
    </div>
  );
}
