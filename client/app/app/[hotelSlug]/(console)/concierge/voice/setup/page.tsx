"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Phone setup guide (03 spec 3.6). Six steps for the hotel's IT or PBX
   engineer: pick the PBX, create the line (dial target plus header
   secret), map extensions to rooms, configure the PBX from the recipe
   (TLS 5061, SRTP, G.711), the regulatory rules (internal calls only, e&
   or du sign-off), then a test call watched live on the webhook panel with
   a checklist. Works before the voice routes are deployed: the recipes and
   rules need no server, and the line-specific values fall back to
   placeholders.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuCopy } from "react-icons/lu";
import { useHotel } from "../../../../../../lib/hotel";
import { Badge, Button, Card, Field, Select, useToast } from "../../../../../../ui";
import { PBX_LABEL, ccHref, vcApi } from "../../lib";
import type { PbxKind, VoiceLine } from "../../types";
import { Checklist } from "./Checklist";
import { NETWORK_REQUIREMENTS, RECIPES, REGULATORY, fill } from "./recipes";
import { useVoiceApi } from "../voiceApi";
import { WebhookPanel } from "./WebhookPanel";

export default function VoiceSetupPage() {
  const { slug } = useHotel();
  const toast = useToast();
  const lines = useVoiceApi<VoiceLine[]>(vcApi(slug, "/lines"), "lines", [slug]);
  const [lineId, setLineId] = useState<number | null>(null);
  const [pbx, setPbx] = useState<PbxKind | null>(null);
  const list = lines.data ?? [];
  const line = list.find(l => l.id === lineId) ?? list[0] ?? null;
  const kind: PbxKind = pbx ?? line?.pbx_kind ?? "grandstream_ucm";
  const recipe = RECIPES.find(r => r.kind === kind) ?? RECIPES[RECIPES.length - 1];
  const vars = { sipUri: line?.sip_uri, projectId: line?.trunk_id };
  const copy = (t: string) => void navigator.clipboard?.writeText(t).then(() => toast.success("Copied"), () => toast.error("Could not copy"));

  // SECTION: steps
  return (
    <>
      <div className="cc-callout">
        <strong>Internal calls only.</strong> The AI answers room phones on the hotel&apos;s own PBX. It never dials outside numbers, and
        transfers go to internal extensions. Get e&amp; or du sign-off before go-live (step 5).
      </div>
      <Card>
        <div className="cc-fields">
          <Field label="Line">
            <Select value={line ? String(line.id) : ""} disabled={!list.length}
              onChange={e => setLineId(Number(e.target.value))}
              options={list.length ? list.map(l => ({ value: String(l.id), label: l.label })) : [{ value: "", label: lines.loading ? "Loading..." : "No line yet" }]} />
          </Field>
          <Field label="Phone system (PBX)">
            <Select value={kind} onChange={e => setPbx(e.target.value as PbxKind)}
              options={(Object.keys(PBX_LABEL) as PbxKind[]).map(k => ({ value: k, label: PBX_LABEL[k] }))} />
          </Field>
        </div>
      </Card>

      <Card>
        <ol className="cc-steps">
          <li className="cc-step">
            <h3 className="cc-h3">Pick the phone system</h3>
            <p className="cc-sub">Chosen above: {PBX_LABEL[kind]}. {recipe.header === "bridge"
              ? "It cannot add the Clefs header itself: identify it by IP (Allowed sources on the line, which always asks the guest for the room) or put a SIP bridge in front so extensions map to verified rooms."
              : recipe.header === "native" ? "It can add the Clefs header itself." : ""}</p>
          </li>
          <li className="cc-step">
            <h3 className="cc-h3">Create the line</h3>
            <p className="cc-sub">The PBX dials this address. The header secret is shown once when the line is created or rotated.</p>
            <div className="cx-stack" style={{ gap: 8, marginTop: 8 }}>
              <div className="cx-row">
                <span className="cc-mono cc-grow">{fill("{SIP_URI}", vars)}</span>
                <Button size="sm" variant="ghost" icon={<LuCopy />} aria-label="Copy dial target" onClick={() => copy(fill("{SIP_URI}", vars))} />
              </div>
              <span className="cc-mono cc-small">X-Clefs-Line: {line?.has_secret ? `vcl_...${line.line_secret_hint ?? ""} (saved on the PBX)` : "YOUR_LINE_SECRET"}</span>
              <div><Button size="sm" variant="secondary" href={ccHref(slug, "/voice")}>{line ? "Manage lines" : "Create the line"}</Button></div>
            </div>
          </li>
          <li className="cc-step">
            <h3 className="cc-h3">Map extensions to rooms</h3>
            <p className="cc-sub">
              A call binds to a room only when its extension is on the line&apos;s list. Use a rule (extension = prefix + room number)
              or a CSV. {line ? `${line.extensions ?? 0} mapped on ${line.label}.` : ""}
              {kind === "fxo_gateway" ? " Analog lines usually lose caller id: set Room from caller id to No and the AI asks for the room." : ""}
            </p>
          </li>
          <li className="cc-step">
            <h3 className="cc-h3">Configure the PBX: {recipe.title}</h3>
            <p className="cc-sub">{recipe.summary}</p>
            <span className="cc-label" style={{ display: "block", marginTop: 10 }}>Every PBX</span>
            <ul className="cc-samples">{NETWORK_REQUIREMENTS.map(r => <li key={r}>{r}</li>)}</ul>
            <span className="cc-label" style={{ display: "block", marginTop: 10 }}>{recipe.title}</span>
            <ol className="cc-samples">{recipe.steps.map(s => <li key={s}>{fill(s, vars)}</li>)}</ol>
            {recipe.snippet && (
              <div className="cx-stack" style={{ gap: 6, marginTop: 10 }}>
                <div className="cc-spread">
                  <span className="cc-label">{recipe.snippet.label}</span>
                  <Button size="sm" variant="ghost" icon={<LuCopy />} onClick={() => copy(fill(recipe.snippet!.text, vars))}>Copy</Button>
                </div>
                <pre className="cc-pre">{fill(recipe.snippet.text, vars)}</pre>
              </div>
            )}
            {recipe.caveats?.map(c => <p key={c} className="cc-sub"><Badge tone="warn">Note</Badge> {c}</p>)}
          </li>
          <li className="cc-step">
            <h3 className="cc-h3">Regulatory</h3>
            <ul className="cc-samples">{REGULATORY.map(r => <li key={r}>{r}</li>)}</ul>
          </li>
          <li className="cc-step">
            <h3 className="cc-h3">Test call</h3>
            <p className="cc-sub">Place the calls below from a room phone and watch the events arrive. Switch the line on when every item passes.</p>
          </li>
        </ol>
      </Card>

      <WebhookPanel slug={slug} lineId={line?.id ?? null} />
      <Checklist lineKey={line ? `line${line.id}` : "noline"} />
    </>
  );
}
