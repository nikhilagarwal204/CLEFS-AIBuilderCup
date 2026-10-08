"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Phone call log (03 spec 3.6 "/voice/calls"): every call attempt, newest
   first, with how it ended, how the room was known (mapped extension,
   spoken by the caller, or unknown), what the AI filed, and transfers or
   emergencies. A row opens the call: its transcript, masked by role like
   every transcript (the server masks; the console also refuses to render
   one for a role the matrix hides), and its tool calls.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuPhoneCall } from "react-icons/lu";
import { withQuery } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useHotel } from "../../../../../../lib/hotel";
import { Badge, Card, EmptyState, Field, Input, Select, Skeleton, Table, type Column } from "../../../../../../ui";
import { CALL_TONE, durationText, errText, isMissingRoute, vcApi } from "../../lib";
import type { CallStatus, VoiceCall, VoiceLine } from "../../types";
import { useVoiceApi } from "../voiceApi";
import { CallDrawer } from "./CallDrawer";

const STATUSES: CallStatus[] = ["active", "ended", "rejected", "failed", "stale"];
const ROOM_HOW: Record<VoiceCall["room_confirmed_by"], string> = { extension: "extension", spoken: "said by caller", none: "unknown" };

export default function VoiceCallsPage() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [lineId, setLineId] = useState("");
  const [status, setStatus] = useState("");
  const [room, setRoom] = useState("");
  const [open, setOpen] = useState<VoiceCall | null>(null);
  const lines = useVoiceApi<VoiceLine[]>(vcApi(slug, "/lines"), "lines", [slug]);
  const path = withQuery(vcApi(slug, "/calls"), {
    limit: 100, line_id: lineId || undefined, status: status || undefined, room: room.trim() || undefined,
  });
  const calls = useVoiceApi<VoiceCall[]>(path, "calls", [path]);

  const columns: Column<VoiceCall>[] = [
    { key: "at", header: "Started", render: c => fmt.dateTime(c.started_at) },
    { key: "room", header: "Room", render: c => c.room
      ? <span>{c.room} <span className="cc-small cx-muted">({ROOM_HOW[c.room_confirmed_by]})</span></span>
      : <span className="cx-faint">{c.from_user ? `ext ${c.from_user}` : "unknown"}</span> },
    { key: "status", header: "Status", render: c => <Badge tone={CALL_TONE[c.status] ?? "neutral"} dot>{c.status}</Badge> },
    { key: "dur", header: "Length", align: "right", render: c => durationText(c.duration_s) },
    { key: "req", header: "Filed", align: "right", hideOnMobile: true, render: c => c.requests_created || "" },
    { key: "flags", header: "", hideOnMobile: true, render: c => (
      <span className="cx-row">
        {c.emergencies > 0 && <Badge tone="bad">Emergency</Badge>}
        {c.transferred && <Badge tone="info">Transferred</Badge>}
        {c.reject_reason && <span className="cc-small cx-muted">{c.reject_reason.replaceAll("_", " ")}</span>}
      </span>
    ) },
  ];

  if (calls.error && isMissingRoute(calls.error)) {
    return <Card><EmptyState icon={<LuPhoneCall />} title="The call log is not available on this server yet"
      body="It appears once the phone module is deployed." /></Card>;
  }
  return (
    <>
      <Card>
        <div className="cc-fields" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
          <Field label="Line"><Select value={lineId} onChange={e => setLineId(e.target.value)}
            options={[{ value: "", label: "All lines" }, ...(lines.data ?? []).map(l => ({ value: String(l.id), label: l.label }))]} /></Field>
          <Field label="Status"><Select value={status} onChange={e => setStatus(e.target.value)}
            options={[{ value: "", label: "All" }, ...STATUSES.map(s => ({ value: s, label: s }))]} /></Field>
          <Field label="Room"><Input value={room} onChange={e => setRoom(e.target.value.slice(0, 12))} placeholder="504" /></Field>
        </div>
      </Card>
      {calls.error && <div className="cc-callout" role="alert">{errText(calls.error)}</div>}
      {calls.loading && !calls.data ? <Skeleton lines={8} /> : (
        <Card padded={false}>
          <Table columns={columns} rows={calls.data ?? []} rowKey={c => c.id} onRowClick={setOpen} caption="Phone calls"
            empty="No calls yet. Place a test call from a room phone." />
        </Card>
      )}
      {open && <CallDrawer slug={slug} call={open} onClose={() => setOpen(null)} />}
    </>
  );
}
