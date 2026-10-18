"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Returning-guest badges ("3rd stay · extra pillows · no nuts"), as the
   queue, device and handover show them. The server composes `label` AFTER
   masking, so a role that may not see allergies never gets them here
   either; this component only lays the badge out.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Badge, EmptyState, Skeleton } from "../../../../ui";
import { gstHref, ordinal } from "./lib";
import type { Badge as BadgeT } from "./types";

export default function BadgeList({
  items, loading, error, empty,
}: {
  items?: BadgeT[];
  loading?: boolean;
  error?: string;
  empty: string;
}) {
  const { slug } = useHotel();
  const fmt = useFormat();
  if (loading) return <Skeleton lines={3} height={44} />;
  if (error) return <div className="gst-muted">{error}</div>;
  if (!items || items.length === 0) return <EmptyState title={empty} />;
  return (
    <div className="gst-badges">
      {items.map(b => (
        <div key={b.stay_id} className="gst-badge-row">
          <div className="gst-badge-row__room">{b.room || "-"}</div>
          <div className="gst-badge-row__body">
            <div className="gst-badge-row__label">
              {b.first_name ? <strong>{b.first_name} </strong> : null}
              {b.label || `${ordinal(b.stay_number)} stay`}
            </div>
            <div className="gst-small gst-muted">
              {fmt.date(b.check_in)} to {fmt.date(b.check_out)}
              {b.health?.allergies?.length ? <> · Allergies: {b.health.allergies.join(", ")}</> : null}
            </div>
          </div>
          <div className="cx-row" style={{ gap: 6 }}>
            {b.vip && <Badge tone="gold">VIP</Badge>}
            <Badge tone="info">{ordinal(b.stay_number)} stay</Badge>
            {b.profile_id != null && (
              <Link className="gst-small" href={gstHref(slug, `/profiles?id=${b.profile_id}`)}>Profile</Link>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
