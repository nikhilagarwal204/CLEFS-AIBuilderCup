"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Events (02 spec 4.4): every event in the window with filters (kind,
   window, cancelled, dismissed, text), each with its sources, confidence,
   last check and change history. Owners add events by hand, import a
   calendar file, dismiss one event or a whole class, and unmute classes.
   Scores and dismissals come from GET /relevance and are joined by id.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Segmented, Select, Skeleton, useToast } from "../../../../../ui";
import AddEventModal from "../_components/AddEventModal";
import EventItem from "../_components/EventItem";
import ImportModal from "../_components/ImportModal";
import { addDays, CATEGORIES, CATEGORY_LABEL } from "../_lib/model";
import { drApi, errorText, PERM } from "../_lib/paths";
import type { ClassPref, DemandEvent, EventCategory, IcsResult, RelevanceRow } from "../_lib/types";

type Window = 30 | 90 | 180;

export default function EventsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const [win, setWin] = useState<Window>(90);
  const [category, setCategory] = useState<EventCategory | "">("");
  const [cancelled, setCancelled] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const manage = can(PERM.manage);

  const [today] = useState(() => fmt.dayKey(Date.now()));
  const from = today;
  const to = addDays(today, win);
  const evs = useApi<DemandEvent[]>(
    withQuery(drApi(slug, "/events"), { from, to, category: category || undefined, include_cancelled: cancelled || undefined }),
    [slug, from, to, category, cancelled],
  );
  const rel = useApi<RelevanceRow[]>(withQuery(drApi(slug, "/relevance"), { from, to, include_dismissed: true }), [slug, from, to]);
  const prefs = useApi<ClassPref[]>(drApi(slug, "/class-prefs"), [slug]);

  const scores = useMemo(() => new Map((rel.data ?? []).map(r => [r.event_id, r])), [rel.data]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (evs.data ?? [])
      .filter(e => showDismissed || !scores.get(e.id)?.dismissed)
      .filter(e => !needle || e.title.toLowerCase().includes(needle) || e.venue_codes.join(" ").toLowerCase().includes(needle));
  }, [evs.data, scores, showDismissed, q]);
  const hiddenDismissed = (evs.data ?? []).filter(e => scores.get(e.id)?.dismissed).length;
  const muted = (prefs.data ?? []).filter(p => p.muted);

  const reload = () => {
    evs.reload();
    rel.reload();
    prefs.reload();
  };

  const unmute = async (key: string) => {
    try {
      await api.put(drApi(slug, `/class-prefs/${encodeURIComponent(key)}`), { muted: false });
      toast.success("Unmuted", key.replace(":", " at ").replaceAll("_", " ").toLowerCase());
      prefs.reload();
      rel.reload();
    } catch (err) {
      toast.error("Could not unmute", errorText(err));
    }
  };

  return (
    <>
      <Card
        title="Events near the hotel"
        subtitle="From the Clefs venue calendar, PredictHQ, Ticketmaster, AI web search (always unverified until confirmed) and your own entries."
        actions={manage ? (
          <>
            <Button size="sm" onClick={() => setImporting(true)}>Import calendar</Button>
            <Button size="sm" variant="primary" onClick={() => setAdding(true)}>Add event</Button>
          </>
        ) : undefined}
      >
        <div className="dr-toolbar" style={{ marginBottom: 14 }}>
          {/* Not a Field: a <label> around a button group would click its first button. */}
          <div className="cx-field">
            <span className="cx-label">Next</span>
            <Segmented<Window> size="sm" ariaLabel="Events in the next" value={win} onChange={setWin}
                               options={[{ value: 30, label: "30 days" }, { value: 90, label: "90" }, { value: 180, label: "180" }]} />
          </div>
          <Field label="Kind">
            <Select value={category} onChange={e => setCategory(e.target.value as EventCategory | "")}>
              <option value="">All kinds</option>
              {CATEGORIES.map(c => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
            </Select>
          </Field>
          <Field label="Search" className="dr-grow">
            <Input type="search" value={q} placeholder="Name or venue" onChange={e => setQ(e.target.value)} />
          </Field>
          <label className="dr-check"><input type="checkbox" checked={cancelled} onChange={e => setCancelled(e.target.checked)} /><span>Cancelled</span></label>
          <label className="dr-check"><input type="checkbox" checked={showDismissed} onChange={e => setShowDismissed(e.target.checked)} /><span>Dismissed{hiddenDismissed ? ` (${hiddenDismissed})` : ""}</span></label>
        </div>

        {evs.error ? (
          <EmptyState title="Could not load events" body={errorText(evs.error)} action={<Button onClick={evs.reload}>Try again</Button>} />
        ) : evs.loading && !evs.data ? (
          <Skeleton lines={6} height={18} />
        ) : rows.length === 0 ? (
          <EmptyState title="No events in this window" body={manage ? "Add the ones you know about, or import your sales team's calendar." : undefined} />
        ) : (
          <ul className="dr-list">
            {rows.map(e => {
              const r = scores.get(e.id);
              return (
                <EventItem key={e.id} slug={slug} event={e} dismissed={r?.dismissed ?? false} onChanged={reload}
                           extra={r ? [`score ${fmt.number(r.score, 2)}`, r.distance_km !== null ? `${fmt.number(r.distance_km, 1)} km` : null].filter(Boolean).join(", ") : undefined} />
              );
            })}
          </ul>
        )}
      </Card>

      {muted.length > 0 && (
        <Card title="Muted kinds" subtitle="These never raise a suggestion. Unmute to score them again.">
          <div className="dr-chips">
            {muted.map(p => (
              <span key={p.class_key} className="cx-row" style={{ gap: 4 }}>
                <Badge>{p.class_key.replace(":", " at ").replaceAll("_", " ").toLowerCase()}</Badge>
                {manage && <Button size="sm" variant="ghost" onClick={() => unmute(p.class_key)}>Unmute</Button>}
              </span>
            ))}
          </div>
        </Card>
      )}

      <AddEventModal slug={slug} open={adding} onClose={() => setAdding(false)} onSaved={reload} />
      <ImportModal<IcsResult>
        open={importing}
        onClose={() => setImporting(false)}
        title="Import a calendar"
        intro="An .ics file from Google Calendar, Outlook or your sales team. Events stay private to this hotel; importing the same file again updates them in place."
        endpoint={drApi(slug, "/events/import-ics")}
        accept=".ics,text/calendar"
        describe={r => `${r.imported} events read: ${r.created} new, ${r.matched} matched known events, ${r.rescheduled} moved.`}
        onDone={reload}
      />
    </>
  );
}
