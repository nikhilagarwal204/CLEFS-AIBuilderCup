"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Venues (hotel_locations) and their notes. A venue is a place with its own
   QR code (pool bar, spa, lobby); a guest scanning it talks to the same
   concierge with that venue's notes added ("Last orders at 22:30, towels
   at the kiosk"). Notes come from two places, both shown here:
     - the venue's own note (hotel_locations.custom_instructions), edited
       by the owner or GM only, which is what "GM-approved" means for it;
     - approved knowledge answers of kind "Venue note" for that venue.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { LuMapPin, LuPlus } from "react-icons/lu";
import { api, hotelPath, useApi, withQuery } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Skeleton, useToast } from "../../../../../ui";
import { PERM, ccApi, ccHref, errText } from "../lib";
import type { KbEntry, Venue } from "../types";
import { VenueModal } from "./VenueModal";

export default function VenuesPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const manage = can(PERM.manage);
  const venues = useApi<Venue[]>(hotelPath(slug, "/locations"), [slug]);
  const notes = useApi<KbEntry[]>(withQuery(ccApi(slug, "/kb/entries"), { status: "approved" }), [slug]);
  const [editing, setEditing] = useState<Venue | null | undefined>(undefined);

  const byVenue = useMemo(() => {
    const m = new Map<string, KbEntry[]>();
    for (const e of notes.data ?? []) {
      if (e.kind !== "venue_note" || !e.location_slug) continue;
      m.set(e.location_slug, [...(m.get(e.location_slug) ?? []), e]);
    }
    return m;
  }, [notes.data]);

  async function remove(v: Venue) {
    if (!window.confirm(`Remove ${v.name}? Its QR code stops working.`)) return;
    try {
      await api.del(hotelPath(slug, `/locations/${encodeURIComponent(v.slug)}`));
      toast.success("Venue removed");
      venues.reload();
    } catch (e) {
      toast.error("Could not remove", errText(e));
    }
  }

  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <>
      <div className="cc-spread">
        <p className="cc-sub" style={{ margin: 0, maxWidth: 640 }}>
          Each venue has its own QR code. Guests who scan it get the concierge plus that venue&apos;s notes.
        </p>
        {manage && <Button icon={<LuPlus />} onClick={() => setEditing(null)}>Add venue</Button>}
      </div>
      {venues.error && <div className="cc-callout" role="alert">{errText(venues.error)}</div>}
      {venues.loading && !venues.data ? <Skeleton lines={5} /> : !venues.data?.length ? (
        <Card><EmptyState icon={<LuMapPin />} title="No venues yet"
          body="Add the pool, the spa or the lobby so guests there can ask the concierge too."
          action={manage ? <Button onClick={() => setEditing(null)}>Add venue</Button> : undefined} /></Card>
      ) : (
        <div className="cx-grid cx-grid--2">
          {venues.data.map(v => {
            const kbNotes = byVenue.get(v.slug) ?? [];
            const url = `${origin}/stay/${encodeURIComponent(slug)}/${encodeURIComponent(v.slug)}`;
            return (
              <Card key={v.slug} title={v.name} subtitle={<span className="cc-mono">{v.slug}</span>}
                actions={<Badge>{v.type}</Badge>}>
                <div className="cx-stack" style={{ gap: 10 }}>
                  <div>
                    <span className="cc-label">Venue note</span>
                    <p className="cc-entry__a" style={{ marginTop: 4 }}>
                      {v.custom_instructions?.trim() || <span className="cx-faint">No note. The concierge uses the hotel knowledge only.</span>}
                    </p>
                  </div>
                  {kbNotes.length > 0 && (
                    <div>
                      <span className="cc-label">Approved venue answers ({kbNotes.length})</span>
                      <ul className="cc-samples">{kbNotes.slice(0, 4).map(e => <li key={e.id}>{e.question_en || e.answer_en}</li>)}</ul>
                    </div>
                  )}
                  <span className="cc-small cx-muted">QR link: <a href={url} target="_blank" rel="noreferrer noopener" className="cc-mono">{url}</a></span>
                  {manage && (
                    <div className="cx-row">
                      <Button size="sm" variant="secondary" onClick={() => setEditing(v)}>Edit</Button>
                      <Button size="sm" variant="ghost" href={ccHref(slug, "/knowledge")}>Add venue answers</Button>
                      <Button size="sm" variant="danger" onClick={() => void remove(v)}>Remove</Button>
                    </div>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
      {editing !== undefined && (
        <VenueModal slug={slug} venue={editing} existing={(venues.data ?? []).map(v => v.slug)}
          onClose={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); venues.reload(); }} />
      )}
    </>
  );
}
