"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Guests overview: the numbers an owner checks first (profiles, returning
   share, today's returning arrivals, consent coverage, direct codes) and
   today's returning arrivals as badges. Counts only; every name and
   allergy in the badge list is already masked for the viewer's role.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Card, EmptyState, StatTile } from "../../../../ui";
import BadgeList from "./BadgeList";
import { gstApi, gstHref, PERM, PURPOSE_LABEL, PURPOSES } from "./lib";
import type { Badge as BadgeT, Overview } from "./types";

export default function GuestsOverview() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const allowed = can(PERM.view);
  const ov = useApi<Overview>(allowed ? gstApi(slug, "/overview") : null, [slug]);
  const arr = useApi<{ day: string; items: BadgeT[] }>(allowed ? gstApi(slug, "/arrivals") : null, [slug]);

  if (!allowed) {
    return <EmptyState title="No access to guests" body="Ask the owner or GM for the guests.view permission." />;
  }
  if (ov.error) {
    return <EmptyState title="Could not load guests" body={ov.error.message} />;
  }
  const d = ov.data;
  const loading = ov.loading && !d;
  const share = d && d.profiles ? (100 * d.returning_profiles) / d.profiles : null;

  return (
    <>
      <div className="cx-grid cx-grid--4">
        <Link className="gst-tile-link" href={gstHref(slug, "/profiles")}>
          <StatTile label="Guest profiles" loading={loading} value={d ? fmt.number(d.profiles) : "-"}
            sub="Only guests who agreed to be remembered" />
        </Link>
        <Link className="gst-tile-link" href={gstHref(slug, "/profiles?returning=1")}>
          <StatTile label="Returning guests" loading={loading} value={d ? fmt.number(d.returning_profiles) : "-"}
            sub={share === null ? "No profiles yet" : `${fmt.pct(share, { digits: 0 })} of profiles; ${fmt.number(d?.vip_profiles ?? 0)} VIP`} />
        </Link>
        <Link className="gst-tile-link" href={gstHref(slug, "/arrivals")}>
          <StatTile label="Returning arrivals today" loading={loading}
            value={d ? fmt.number(d.returning_arrivals_today) : "-"}
            sub={d && !d.returning_badge ? "Badge hidden on the queue (settings)" : "Shown on the queue, device and handover"} />
        </Link>
        <Link className="gst-tile-link" href={gstHref(slug, "/rebooking")}>
          <StatTile label="Direct codes redeemed" loading={loading}
            value={d ? `${fmt.number(d.direct_codes.redeemed)} / ${fmt.number(d.direct_codes.issued)}` : "-"}
            sub="Redeemed of issued" />
        </Link>
      </div>

      <div className="cx-grid cx-grid--2">
        <Card title="Consent on record" subtitle="People whose latest decision is a yes, per purpose">
          {loading ? <div className="gst-muted">Loading...</div> : (
            <div className="gst-consents">
              {PURPOSES.map(p => (
                <div key={p} className="gst-consent">
                  <span>{PURPOSE_LABEL[p]}</span>
                  <strong className="gst-num">{fmt.number(d?.consents?.[p] ?? 0)}</strong>
                </div>
              ))}
              <div className="gst-consent">
                <span>Opted out (STOP or unsubscribe)</span>
                <Badge tone={d?.suppressed_contacts ? "warn" : "neutral"}>{fmt.number(d?.suppressed_contacts ?? 0)}</Badge>
              </div>
            </div>
          )}
          <p className="gst-note" style={{ marginTop: 10 }}>
            Marketing goes only to guests who gave their own number or email with a separate yes. OTA booking
            contacts are never marketed to. <Link href={gstHref(slug, "/consent")}>Look up one guest</Link>
          </p>
        </Card>

        <Card title="Returning arrivals today" subtitle={arr.data ? fmt.date(arr.data.day, "medium") : undefined}
          actions={<Link className="gst-small" href={gstHref(slug, "/arrivals")}>Other days</Link>}>
          <BadgeList items={arr.data?.items} loading={arr.loading && !arr.data} error={arr.error?.message}
            empty="No returning guests arrive today." />
        </Card>
      </div>
    </>
  );
}
