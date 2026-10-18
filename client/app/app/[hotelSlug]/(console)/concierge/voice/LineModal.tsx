"use client";

/* Create or edit a phone line (vc_lines). The client repeats the server's
   two safety rules so the manager sees them before saving:
     - the front desk transfer target is sip: or sips: only, never tel:
       (PSTN breakout through the telephony bridge is a UAE regulatory grey area);
     - allowed sources are domains, IPs or CIDRs (for a PBX such as the
       Grandstream UCM that cannot add the X-Clefs-Line header). They need
       the SIP trunk id, and because a caller can forge those hosts a
       call matched this way always asks for the room (never verified).
   A new line starts switched off: it goes live after the test call. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { LANGUAGE_NAMES, PBX_LABEL, errText, vcApi } from "../lib";
import type { AfterHours, LineWithSecret, PbxKind, VoiceLine } from "../types";
import { unwrapLine } from "./voiceApi";

const SIP_RE = /^sips?:\S+$/i;
const VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"];

export function LineModal({ slug, line, onClose, onSaved }: {
  slug: string;
  line: VoiceLine | null;
  onClose: () => void;
  onSaved: (line: VoiceLine, secret?: string) => void;
}) {
  const toast = useToast();
  const ah = (line?.after_hours ?? {}) as Partial<AfterHours>;
  const [f, setF] = useState({
    label: line?.label ?? "In-room phone",
    trunk_id: line?.trunk_id ?? "",
    pbx_kind: (line?.pbx_kind ?? "grandstream_ucm") as PbxKind,
    caller_id_mode: line?.caller_id_mode ?? "extension",
    front_desk_uri: line?.front_desk_uri ?? "",
    outside_line_prefix: line?.outside_line_prefix ?? "",
    default_language: line?.default_language ?? "en",
    voice: line?.voice ?? "marin",
    max_call_min: String(Math.round((line?.max_call_s ?? 1800) / 60)),
    max_concurrent: String(line?.max_concurrent ?? 4),
    tool_transport: line?.tool_transport ?? "mcp",
    sources: (line?.allowed_sources ?? []).join("\n"),
    ah_enabled: !!ah.enabled, ah_start: ah.start ?? "23:00", ah_end: ah.end ?? "06:00",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f, v: string | boolean) => setF(p => ({ ...p, [k]: v }));

  const fdErr = f.front_desk_uri.trim() && !SIP_RE.test(f.front_desk_uri.trim())
    ? "Use a sip: or sips: address (for example sip:100@pbx.hotel.local). Phone numbers (tel:) are not allowed." : undefined;
  const mins = Number(f.max_call_min), conc = Number(f.max_concurrent);

  async function save() {
    if (fdErr) { setErr(fdErr); return; }
    if (!Number.isInteger(mins) || mins < 1 || mins > 120) { setErr("Longest call is 1 to 120 minutes."); return; }
    if (!Number.isInteger(conc) || conc < 1 || conc > 50) { setErr("Calls at once is 1 to 50."); return; }
    const body = {
      label: f.label.trim(), trunk_id: f.trunk_id.trim() || null, pbx_kind: f.pbx_kind,
      caller_id_mode: f.caller_id_mode, front_desk_uri: f.front_desk_uri.trim() || null,
      outside_line_prefix: f.outside_line_prefix.trim() || null, default_language: f.default_language,
      voice: f.voice, max_call_s: mins * 60, max_concurrent: conc, tool_transport: f.tool_transport,
      allowed_sources: f.sources.split(/[\s,]+/).map(s => s.trim()).filter(Boolean),
      after_hours: f.ah_enabled ? { enabled: true, start: f.ah_start, end: f.ah_end } : { enabled: false },
    };
    setBusy(true);
    setErr(null);
    try {
      if (line) {
        onSaved(unwrapLine(await api.patch<unknown>(vcApi(slug, `/lines/${line.id}`), body)));
        toast.success("Line saved");
      } else {
        const out = await api.post<LineWithSecret>(vcApi(slug, "/lines"), { ...body, enabled: false });
        onSaved(out.line, out.secret);
      }
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} size="lg" title={line ? `Edit ${line.label}` : "New phone line"} footer={
      <div className="cx-row">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button loading={busy} onClick={() => void save()}>{line ? "Save" : "Create line"}</Button>
      </div>
    }>
      <div className="cx-stack">
        <div className="cc-fields">
          <Field label="Name" required><Input value={f.label} onChange={e => set("label", e.target.value)} /></Field>
          <Field label="Phone system"><Select value={f.pbx_kind} onChange={e => set("pbx_kind", e.target.value)}
            options={(Object.keys(PBX_LABEL) as PbxKind[]).map(k => ({ value: k, label: PBX_LABEL[k] }))} /></Field>
          <Field label="SIP trunk id" hint="From the SIP trunk that receives the calls (proj_...).">
            <Input className="cc-mono" value={f.trunk_id} onChange={e => set("trunk_id", e.target.value)} placeholder="proj_..." />
          </Field>
          <Field label="Room from caller id"><Select value={f.caller_id_mode} onChange={e => set("caller_id_mode", e.target.value)}
            options={[{ value: "extension", label: "Yes: the extension identifies the room" }, { value: "none", label: "No: ask the caller (FXO lines)" }]} /></Field>
          <Field label="Front desk transfer" error={fdErr} hint="sip: or sips: only. Internal transfers, never an outside number.">
            <Input className="cc-mono" value={f.front_desk_uri} onChange={e => set("front_desk_uri", e.target.value)} placeholder="sip:100@pbx.hotel.local" />
          </Field>
          <Field label="Outside line prefix" hint="Digits a guest dials before 999 (often 0 or 9). Used in the emergency answer.">
            <Input value={f.outside_line_prefix} onChange={e => set("outside_line_prefix", e.target.value.replace(/[^0-9*#]/g, "").slice(0, 4))} />
          </Field>
          <Field label="Opening language"><Select value={f.default_language} onChange={e => set("default_language", e.target.value)}
            options={Object.entries(LANGUAGE_NAMES).map(([v, l]) => ({ value: v, label: l }))} /></Field>
          <Field label="Voice"><Select value={f.voice} onChange={e => set("voice", e.target.value)} options={VOICES.map(v => ({ value: v, label: v }))} /></Field>
          <Field label="Longest call (minutes)"><Input type="number" min={1} max={120} value={f.max_call_min} onChange={e => set("max_call_min", e.target.value)} /></Field>
          <Field label="Calls at once"><Input type="number" min={1} max={50} value={f.max_concurrent} onChange={e => set("max_concurrent", e.target.value)} /></Field>
          <Field label="Tool transport" hint="MCP needs no extra server. Sideband needs the Clefs voice worker running.">
            <Select value={f.tool_transport} onChange={e => set("tool_transport", e.target.value)}
              options={[{ value: "mcp", label: "MCP (default)" }, { value: "sideband", label: "Sideband worker" }]} />
          </Field>
          <Field label="Allowed sources" className="cc-fields__wide" hint="Only for a PBX that cannot add the header: its public IP, CIDR or SIP domain, one per line. Needs the SIP trunk id. These calls always ask the guest for the room.">
            <Textarea rows={2} className="cc-mono" value={f.sources} onChange={e => set("sources", e.target.value)} />
          </Field>
        </div>
        <label className="cc-check">
          <input type="checkbox" checked={f.ah_enabled} onChange={e => set("ah_enabled", e.target.checked)} />
          <span>Send calls to the operator overnight (the PBX gets a busy signal and rings its fallback)</span>
        </label>
        {f.ah_enabled && (
          <div className="cc-fields">
            <Field label="From (hotel time)"><Input type="time" value={f.ah_start} onChange={e => set("ah_start", e.target.value)} /></Field>
            <Field label="Until"><Input type="time" value={f.ah_end} onChange={e => set("ah_end", e.target.value)} /></Field>
          </div>
        )}
        {err && <div className="cc-callout" role="alert">{err}</div>}
      </div>
    </Modal>
  );
}
