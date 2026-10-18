"use client";

/* How the neighbours are rated on Google. Ratings come live from the Places
   API on every view and are never stored (Google's terms), so this tab says
   so and credits Google; only the place ids are saved. The hotel's own place
   sits in the same list, highlighted, so the gap reads at a glance. */

import { useState } from "react";
import { ApiError, apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { ShareMeter, CHART_DONE, CHART_NEW } from "../../../charts";
import { Badge, Button, Card, EmptyState, Field, Input, Skeleton, Textarea, cx, useToast } from "../../../../ui";
import { errText } from "./lib";
import type { Competitors } from "./types";

export default function CompetitorsTab({ slug, canManage }: { slug: string; canManage: boolean }) {
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<Competitors>(hotelPath(slug, "/reviews/competitors"), [slug]);
  const [editing, setEditing] = useState(false);
  const [ids, setIds] = useState("");
  const [own, setOwn] = useState("");
  const [saving, setSaving] = useState(false);

  const notConfigured = res.error instanceof ApiError && res.error.code === "not_configured";

  const startEdit = () => {
    setIds((res.data?.place_ids ?? []).join("\n"));
    setOwn(res.data?.own_place_id ?? "");
    setEditing(true);
  };

  const save = async () => {
    const list = ids.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
    if (list.length > 10) { toast.error("Too many places", "Track up to 10 competitors."); return; }
    setSaving(true);
    try {
      await apiFetch(hotelPath(slug, "/reviews/competitors"), {
        method: "PUT", json: { place_ids: list, own_place_id: own.trim() || null },
      });
      toast.success("Competitors saved");
      setEditing(false);
      res.reload();
    } catch (err) {
      toast.error("Could not save competitors", errText(err));
    } finally {
      setSaving(false);
    }
  };

  const places = [...(res.data?.places ?? [])].sort((a, b) => (b.rating ?? -1) - (a.rating ?? -1));

  return (
    <Card
      title="Competitor ratings"
      subtitle="Google rating and review count for the hotels you compete with, fetched live."
      actions={canManage && !editing && !notConfigured ? <Button size="sm" variant="secondary" onClick={startEdit}>Edit list</Button> : undefined}
    >
      {editing ? (
        <div className="rev-form">
          <Field label="Competitor Google place ids" hint="One per line, up to 10. Find a place id with Google's Place ID Finder.">
            <Textarea code rows={6} value={ids} onChange={e => setIds(e.target.value)} placeholder="ChIJ..." />
          </Field>
          <Field label="Your hotel's place id (optional)" hint="Shown highlighted next to the competitors.">
            <Input value={own} onChange={e => setOwn(e.target.value)} placeholder="ChIJ..." />
          </Field>
          <div className="rev-actions">
            <Button onClick={() => void save()} loading={saving}>Save</Button>
            <Button variant="ghost" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
          </div>
        </div>
      ) : res.loading && !res.data ? (
        <Skeleton lines={5} />
      ) : notConfigured ? (
        <EmptyState title="Competitor ratings are not set up" body={errText(res.error)} />
      ) : res.error ? (
        <EmptyState title="Could not load competitor ratings" body={errText(res.error)}
          action={<Button onClick={res.reload}>Try again</Button>} />
      ) : places.length === 0 ? (
        <EmptyState title="No competitors yet"
          body={canManage ? "Add the Google place ids of the hotels you compete with." : "The owner or GM can add competitors."}
          action={canManage ? <Button onClick={startEdit}>Add competitors</Button> : undefined} />
      ) : (
        <>
          <div role="list" aria-label="Competitor ratings">
            {places.map(p => (
              <div role="listitem" key={p.place_id} className={cx("rev-comp", p.own && "rev-comp--own")}>
                <div>
                  <span className="rev-comp__name">
                    {p.maps_url ? <a href={p.maps_url} target="_blank" rel="noopener noreferrer">{p.name ?? p.place_id}</a> : (p.name ?? p.place_id)}
                  </span>
                  {p.own && <> <Badge tone="gold">You</Badge></>}
                  {p.error && <div className="rev-muted">Could not fetch: {p.error}</div>}
                </div>
                <div className="rev-comp__score">
                  {p.rating != null ? p.rating.toFixed(1) : "-"}
                  <div className="rev-muted" style={{ fontWeight: 400 }}>
                    {p.user_rating_count != null ? `${fmt.number(p.user_rating_count)} reviews` : ""}
                  </div>
                </div>
                {p.rating != null && (
                  <div style={{ gridColumn: "1 / -1" }}>
                    <ShareMeter value={p.rating} max={5} color={p.own ? CHART_NEW : CHART_DONE}
                      title={`${p.name ?? p.place_id}: ${p.rating.toFixed(1)} of 5`} />
                  </div>
                )}
              </div>
            ))}
          </div>
          <p className="rev-muted" style={{ marginBottom: 0 }}>
            Ratings from {res.data?.attribution ?? "Google"}. Fetched live and not stored.
          </p>
        </>
      )}
    </Card>
  );
}
