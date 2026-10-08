"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Campaign drafts (02 spec 4.3 graph actions): WhatsApp, Instagram and
   email copy for one event, written for the hotel's source markets and
   sent to consented direct contacts only. Export only: Clefs drafts, the
   hotel sends. Drafts are grouped by event, newest event first.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, Field, Select, Skeleton, useToast } from "../../../../../ui";
import CampaignCard from "../_components/CampaignCard";
import { addDays, CHANNEL_LABEL } from "../_lib/model";
import { drApi, errorText, PERM } from "../_lib/paths";
import type { Campaign, CampaignChannel, DemandEvent } from "../_lib/types";

const CHANNELS: CampaignChannel[] = ["WHATSAPP", "INSTAGRAM", "EMAIL"];

export default function CampaignsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const [today] = useState(() => fmt.dayKey(Date.now()));
  const [eventId, setEventId] = useState("");
  const [channels, setChannels] = useState<Set<CampaignChannel>>(() => new Set(CHANNELS));
  const [busy, setBusy] = useState(false);
  const manage = can(PERM.manage);

  const drafts = useApi<Campaign[]>(drApi(slug, "/campaigns"), [slug]);
  const evs = useApi<DemandEvent[]>(withQuery(drApi(slug, "/events"), { from: addDays(today, -60), to: addDays(today, 240) }), [slug, today]);
  const byId = useMemo(() => new Map((evs.data ?? []).map(e => [e.id, e])), [evs.data]);
  const upcoming = useMemo(() => (evs.data ?? []).filter(e => e.end_date >= today && e.status !== "CANCELLED" && e.actionable), [evs.data, today]);

  const groups = useMemo(() => {
    const m = new Map<string, Campaign[]>();
    for (const c of drafts.data ?? []) m.set(c.event_id, [...(m.get(c.event_id) ?? []), c]);
    return [...m.entries()].sort((a, b) => (byId.get(b[0])?.start_date ?? "").localeCompare(byId.get(a[0])?.start_date ?? ""));
  }, [drafts.data, byId]);

  const create = async () => {
    if (!eventId || !channels.size) return;
    setBusy(true);
    try {
      const made = await api.post<Campaign[]>(drApi(slug, "/campaigns"), { event_id: eventId, channels: [...channels] });
      toast.success("Drafts ready", `${made.length} draft${made.length === 1 ? "" : "s"} for ${byId.get(eventId)?.title ?? "the event"}.`);
      drafts.reload();
    } catch (err) {
      toast.error("Could not draft the campaign", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (ch: CampaignChannel, on: boolean) => setChannels(prev => {
    const next = new Set(prev);
    if (on) next.add(ch); else next.delete(ch);
    return next;
  });

  return (
    <>
      {manage && (
        <Card title="Draft a campaign for an event" subtitle="Copy for each channel, written from the event facts. Audiences are consented direct guests only; OTA guests are never included. Nothing is sent from Clefs.">
          <div className="dr-toolbar">
            <Field label="Event" className="dr-grow">
              <Select value={eventId} onChange={e => setEventId(e.target.value)} disabled={busy}>
                <option value="">{evs.loading ? "Loading events..." : "Pick an upcoming event"}</option>
                {upcoming.map(e => <option key={e.id} value={e.id}>{e.title} ({fmt.date(e.start_date, "medium")})</option>)}
              </Select>
            </Field>
            <fieldset className="cx-row" style={{ border: 0, padding: 0, margin: 0, gap: 12 }}>
              <legend className="cx-sr">Channels</legend>
              {CHANNELS.map(ch => (
                <label key={ch} className="dr-check">
                  <input type="checkbox" checked={channels.has(ch)} disabled={busy} onChange={e => toggle(ch, e.target.checked)} />
                  <span>{CHANNEL_LABEL[ch]}</span>
                </label>
              ))}
            </fieldset>
            <Button variant="primary" loading={busy} disabled={!eventId || !channels.size} onClick={create}>Draft copy</Button>
          </div>
          <p className="dr-note" style={{ marginTop: 8 }}>Unverified events stay out of this list until you confirm them on the Events tab.</p>
        </Card>
      )}

      {drafts.error ? (
        <EmptyState title="Could not load campaigns" body={errorText(drafts.error)} action={<Button onClick={drafts.reload}>Try again</Button>} />
      ) : drafts.loading && !drafts.data ? (
        <Skeleton lines={5} height={20} />
      ) : groups.length === 0 ? (
        <EmptyState title="No campaign drafts yet" body={manage ? "Pick an event above to draft WhatsApp, Instagram and email copy." : undefined} />
      ) : (
        groups.map(([eid, list]) => {
          const ev = byId.get(eid);
          return (
            <Card key={eid} title={ev?.title ?? "Event"} subtitle={ev ? `${fmt.date(ev.start_date, "medium")}${ev.end_date !== ev.start_date ? ` to ${fmt.date(ev.end_date, "medium")}` : ""}` : undefined}>
              <ul className="dr-list">
                {list.map(c => (
                  <CampaignCard key={c.id} slug={slug} c={c}
                                onChanged={next => (next ? drafts.setData(prev => (prev ?? []).map(x => (x.id === next.id ? next : x))) : drafts.reload())} />
                ))}
              </ul>
            </Card>
          );
        })
      )}
    </>
  );
}
