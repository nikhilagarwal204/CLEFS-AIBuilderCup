"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One request in detail, for the manager (05 spec: "Manager: sees and
   approves"): the job, who has it and since when, the proof photo Gemini
   checked (and why it was rejected), where the guest's answer stands, and
   the timeline from GET /service/requests/{id}. Opened from a card on the
   service Dashboard.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { useFormat } from "../../../../../lib/format";
import { Drawer } from "../../../../../ui";
import { SEED_DEPARTMENTS } from "./catalog";
import {
  emergencyChip, escalationChip, etaChip, eventText, guestChip, moodChip, photoChip, photoSrc, photoState, priorityLabel,
  timeTaken, toMs,
} from "./deskLabels";
import { ChipView, OriginalToggle, ProofAlert, StateChip, useNow } from "./RequestMeta";
import { LANGUAGE_LABELS, SOURCE_LABELS, fetchRequest, type ServiceCard, type ServiceEvent } from "./serviceApi";

type DetailRow = Partial<ServiceCard> & Pick<ServiceCard, "id">;

function teamName(r: DetailRow): string | null {
  const key = r.department_key ?? null;
  const hit = key ? SEED_DEPARTMENTS.find(d => d.key === key) : null;
  return hit?.name ?? r.category ?? null;
}

export default function RequestDetail({ hotelSlug, request, onClose }: {
  hotelSlug: string;
  /** The card as the dashboard holds it; refreshed from the server on open. */
  request: DetailRow | null;
  onClose: () => void;
}) {
  const hf = useFormat();
  const now = useNow(1_000);
  const [loaded, setLoaded] = useState<{ id: number; request: ServiceCard; events: ServiceEvent[] } | null>(null);
  const [failed, setFailed] = useState<number | null>(null);
  const id = request?.id ?? null;

  useEffect(() => {
    if (id === null) return;
    let live = true;
    fetchRequest(hotelSlug, id)
      .then(out => { if (live) setLoaded({ id, request: out.request, events: out.events ?? [] }); })
      .catch(() => { if (live) setFailed(id); });
    return () => { live = false; };
  }, [hotelSlug, id]);

  if (!request || id === null) return null;
  const fresh = loaded?.id === id ? loaded : null;
  // The live card wins over the snapshot for fields the stream refreshed since.
  const r: DetailRow = { ...(fresh?.request ?? {}), ...request };
  const events = fresh?.events ?? [];
  const time = (v: unknown) => {
    const ms = toMs(v);
    return ms === null ? null : new Date(ms).toLocaleTimeString(hf.locale, { hour: "2-digit", minute: "2-digit", timeZone: hf.tz });
  };
  const photo = photoSrc(r.proof_photo_url);
  const pState = photoState(r);
  const taken = timeTaken(r, now);
  const eta = etaChip(r, now);
  const team = teamName(r);
  const lang = r.language && r.language !== "en" ? LANGUAGE_LABELS[r.language] ?? r.language.toUpperCase() : null;

  return (
    <Drawer open onClose={onClose} wide title={`Room ${r.room || "-"} · ${r.type || "Request"}`}>
      <div className="svc-detail">
        <div className="svc-chips">
          <ChipView chip={emergencyChip(r)} />
          {r.priority === "high" && <span className="svc-chip svc-chip--bad">{priorityLabel(r.priority).toUpperCase()}</span>}
          <StateChip req={{ ...r, status: r.status ?? "new" }} now={now} />
          <ChipView chip={escalationChip(r)} />
          <ChipView chip={photoChip(r, true)} />
          <ChipView chip={guestChip(r)} />
        </div>

        <div>
          <p style={{ margin: 0, fontSize: "0.95rem", lineHeight: 1.5 }} dir="auto">{r.description}</p>
          <OriginalToggle req={{ original_text: r.original_text, language: r.language, translated: r.translated, description: r.description }} />
        </div>

        <dl className="svc-detail__facts">
          {team && (<><dt>Team</dt><dd>{team}</dd></>)}
          <dt>Staff member</dt><dd>{r.assigned_to || "Not assigned yet"}</dd>
          {taken && (<><dt>Time taken</dt><dd>{taken.label}</dd></>)}
          {eta && (
            <>
              <dt>ETA</dt>
              <dd>
                {`${r.eta_minutes} min`}
                {toMs(r.eta_at) ? `, due ${time(r.eta_at)}` : ""}
                {eta.tone === "warn" ? " (passed)" : ""}
              </dd>
            </>
          )}
          <dt>Filed</dt><dd>{time(r.timestamp) ?? "-"}{r.source ? ` · ${SOURCE_LABELS[r.source] ?? r.source}` : ""}{lang ? ` · guest spoke ${lang}` : ""}</dd>
          {toMs(r.resolved_at) !== null && (<><dt>Done</dt><dd>{time(r.resolved_at)}</dd></>)}
          {moodChip(r.mood, r.frustration) && (<><dt>Guest mood</dt><dd><ChipView chip={moodChip(r.mood, r.frustration)} /></dd></>)}
        </dl>

        {(photo || pState) && (
          <section>
            <h3 className="svc-detail__h">Proof photo</h3>
            {photo ? (
              <a href={photo} target="_blank" rel="noreferrer" title="Open the full photo">
                {/* eslint-disable-next-line @next/next/no-img-element -- a short-lived signed URL; next/image would cache it */}
                <img className="svc-detail__photo" src={photo} alt={`Completion photo for room ${r.room || ""}`} />
              </a>
            ) : (
              <p className="svc-faint" style={{ margin: 0 }}>The photo is being checked.</p>
            )}
            <ProofAlert req={r} />
            {pState === "verified" && (
              <p className="svc-faint" style={{ margin: "8px 0 0" }}>Gemini checked this photo against the request before the job closed.</p>
            )}
          </section>
        )}

        <section>
          <h3 className="svc-detail__h">Timeline</h3>
          {events.length ? (
            <ol className="svc-timeline">
              {events.map(ev => (
                <li key={ev.id}>
                  <time dateTime={ev.at ?? undefined}>{time(ev.at) ?? ""}</time>
                  <span dir="auto">{eventText(ev)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="svc-faint" style={{ margin: 0 }}>{failed === id ? "Could not load the timeline." : fresh ? "No events yet." : "Loading..."}</p>
          )}
        </section>
      </div>
    </Drawer>
  );
}
