"use client";

/* What extraction read out of the thread, with the verbatim quote behind
   each field (4.4: every extracted field carries evidence), so a person can
   see why the dates say what they say and fix them. Saving is PATCH /{id}:
   the server recomputes the SLA due time and money at risk, and a segment
   set here is never overwritten by a later extraction. Below: the contact
   (masked by role on the server), merge suggestions and the stage history. */

import { useState } from "react";
import { api } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, ConfidencePill, useToast } from "../../../../ui";
import DetailsForm from "./DetailsForm";
import { errorText, inqPath, SEGMENT_LABEL, SOURCE_LABEL, STAGE_LABEL } from "./lib";
import type { InquiryDetail } from "./types";

const FIELD_LABEL: Record<string, string> = {
  check_in: "Check-in", check_out: "Check-out", nights: "Nights", rooms: "Rooms", adults: "Adults",
  children: "Children", segment: "Segment", company: "Company", budget_amount: "Budget", room_type_hint: "Room type",
  event_name: "Event", date_text: "Dates as written", language: "Language", intent: "Intent",
};

export default function DetailsPanel({ row, onChanged, onOpen }: {
  row: InquiryDetail;
  onChanged: () => void;
  onOpen: (id: number) => void;
}) {
  const fmt = useFormat();
  const { can } = useSession();
  const [editing, setEditing] = useState(false);
  const ex = row.extraction;
  const conf = row.extraction_confidence == null ? null : Number(row.extraction_confidence);
  const day = (d: string | null) => (d ? fmt.date(`${d}T12:00:00Z`, "medium") : "Not given");

  if (editing) {
    return <DetailsForm row={row} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(); }} />;
  }

  return (
    <div>
      <section className="inq-section">
        <div className="inq-row inq-row--between">
          <h3>Request</h3>
          <span className="inq-row">
            {conf != null && <ConfidencePill value={conf} />}
            {ex?.reader === "deterministic" && <Badge title="Read by the date and guest-count parser, not the AI">Parser</Badge>}
            {can("inquiries.respond") && <Button size="sm" onClick={() => setEditing(true)}>Edit</Button>}
          </span>
        </div>
        <dl className="inq-kv">
          <dt>Segment</dt><dd>{SEGMENT_LABEL[row.segment]}{row.is_rfp ? " (RFP)" : ""}</dd>
          <dt>Dates</dt>
          <dd>{day(row.check_in)} to {day(row.check_out)}{row.nights ? `, ${row.nights} nights` : ""}
            {row.dates_flexible ? " (flexible)" : ""}</dd>
          <dt>Rooms</dt><dd>{row.rooms ?? "Not given"}{row.room_type_code ? ` (${row.room_type_code})` : ""}</dd>
          <dt>Guests</dt>
          <dd>{row.adults ?? "?"} adults{row.children ? `, ${row.children} children` : ""}</dd>
          {row.budget_value && <><dt>Budget</dt><dd>{row.budget_currency} {row.budget_value}</dd></>}
          {row.company && <><dt>Company</dt><dd>{row.company}</dd></>}
          <dt>Language</dt><dd>{row.language ?? "Not detected"}</dd>
          {ex?.special_requests?.length ? <><dt>Asks for</dt><dd>{ex.special_requests.join("; ")}</dd></> : null}
          {ex?.missing?.length ? <><dt>Missing</dt><dd><Badge tone="warn">{ex.missing.join(", ")}</Badge></dd></> : null}
          {ex?.warnings?.length ? <><dt>Check</dt><dd>{ex.warnings.map(String).join("; ")}</dd></> : null}
        </dl>
        {ex?.evidence?.length ? (
          <div style={{ display: "grid", gap: 6 }}>
            <span className="inq-muted">Where this came from</span>
            {ex.evidence.map((e, i) => (
              <blockquote key={i} className="inq-evidence" dir="auto">
                <b>{FIELD_LABEL[e.field] ?? e.field}</b>&ldquo;{e.quote}&rdquo;
              </blockquote>
            ))}
          </div>
        ) : !ex ? <p className="inq-muted">Not read yet. Extraction runs a few seconds after a message arrives.</p> : null}
      </section>
      <section className="inq-section">
        <h3>Contact and source</h3>
        <dl className="inq-kv">
          <dt>Name</dt><dd>{row.contact_name || "Not given"}</dd>
          <dt>Phone</dt><dd dir="ltr">{row.contact_phone || (row.has_phone ? "Hidden for your role" : "None")}</dd>
          <dt>Email</dt><dd>{row.contact_email || (row.has_email ? "Hidden for your role" : "None")}</dd>
          <dt>Came from</dt><dd>{SOURCE_LABEL[row.source] ?? row.source}{row.channel_ref ? ` (${row.channel_ref})` : ""}</dd>
          <dt>Consent</dt><dd>{CONSENT_TEXT[row.consent_basis] ?? row.consent_basis}</dd>
          {row.ad_referral && (
            <><dt>Ad</dt><dd>{String(row.ad_referral.headline ?? row.ad_referral.source_type ?? "Click-to-WhatsApp ad")}</dd></>
          )}
          <dt>Received</dt><dd>{fmt.dateTime(row.created_at, "medium")}</dd>
        </dl>
      </section>
      {row.merge_suggestions.length > 0 && can("inquiries.respond") && (
        <section className="inq-section">
          <h3>Possible duplicates</h3>
          <p className="inq-muted" style={{ margin: 0 }}>
            Same name or overlapping dates. Merging moves this thread into the other inquiry and closes this one.
          </p>
          {row.merge_suggestions.map(m => (
            <div key={m.id} className="inq-row inq-row--between">
              <span>
                <button type="button" className="inq-chip" onClick={() => onOpen(m.id)}>{m.ref.toUpperCase()}</button>{" "}
                <span className="inq-muted">
                  {SOURCE_LABEL[m.source] ?? m.source}, {STAGE_LABEL[m.stage] ?? m.stage}
                  {m.check_in ? `, ${fmt.date(`${m.check_in}T12:00:00Z`)}` : ""}
                </span>
              </span>
              <MergeButton inquiryId={row.id} intoId={m.id} intoRef={m.ref} onMerged={() => { onChanged(); onOpen(m.id); }} />
            </div>
          ))}
        </section>
      )}
      <section className="inq-section">
        <h3>History</h3>
        <ul className="inq-list">
          {row.stage_events.map((e, i) => (
            <li key={i}>
              {fmt.dateTime(e.at, "medium")}: {e.from_stage ? `${STAGE_LABEL[e.from_stage] ?? e.from_stage} to ` : ""}
              {STAGE_LABEL[e.to_stage] ?? e.to_stage}
              {e.actor_type ? <span className="inq-muted"> by {e.actor_type === "user" ? "staff" : e.actor_type}</span> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

const CONSENT_TEXT: Record<string, string> = {
  express_web_form: "Given on the website form",
  pre_contract_notice: "Guest wrote first (notice sent with the first reply)",
  staff_logged: "Logged by staff",
};

function MergeButton({ inquiryId, intoId, intoRef, onMerged }: {
  inquiryId: number; intoId: number; intoRef: string; onMerged: () => void;
}) {
  const { slug } = useHotel();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function merge() {
    if (!window.confirm(`Merge this inquiry into ${intoRef.toUpperCase()}? This one will close as a duplicate.`)) return;
    setBusy(true);
    try {
      await api.post(inqPath(slug, `/${inquiryId}/merge`), { into_id: intoId });
      toast.success(`Merged into ${intoRef.toUpperCase()}`);
      onMerged();
    } catch (err) {
      toast.error("Not merged", errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return <Button size="sm" loading={busy} onClick={merge}>Merge into it</Button>;
}
