"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Phone lines. One line is one route from the hotel PBX to the the telephony bridge SIP
   endpoint (sip:<project>@sip.voice.clefs.app;transport=tls). Per line: the
   dial target, whether its secret is set, how many extensions map to rooms,
   the front desk transfer target, and the switch that puts it live.

   The voice console routes are the voice module's (/hotels/{slug}/voice);
   while they are still being deployed this page says so instead of showing
   an error wall, and the setup guide below stays fully usable.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuCopy, LuPhone, LuPlus } from "react-icons/lu";
import { api } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Skeleton, useToast } from "../../../../../ui";
import { PBX_LABEL, PERM, ccHref, durationText, errText, isMissingRoute, vcApi } from "../lib";
import type { LineWithSecret, VoiceLine } from "../types";
import { ExtensionsDrawer } from "./ExtensionsDrawer";
import { LineModal } from "./LineModal";
import { SecretReveal } from "./SecretReveal";
import { useVoiceApi } from "./voiceApi";

export default function VoiceLinesPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const manage = can(PERM.voiceManage);
  const lines = useVoiceApi<VoiceLine[]>(vcApi(slug, "/lines"), "lines", [slug]);
  const [editing, setEditing] = useState<VoiceLine | null | undefined>(undefined);
  const [secret, setSecret] = useState<{ secret: string; sipUri: string | null } | null>(null);
  const [exts, setExts] = useState<VoiceLine | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const copy = (t: string) => void navigator.clipboard?.writeText(t).then(() => toast.success("Copied"), () => toast.error("Could not copy"));

  async function act(key: string, fn: () => Promise<unknown>, done: string) {
    setBusy(key);
    try {
      await fn();
      toast.success(done);
      lines.reload();
    } catch (e) {
      toast.error("Could not save", errText(e));
    } finally {
      setBusy(null);
    }
  }

  const rotate = (l: VoiceLine) => {
    if (!window.confirm("Rotate the secret? Calls stop being accepted until the PBX has the new one.")) return;
    setBusy(`r${l.id}`);
    api.post<LineWithSecret>(vcApi(slug, `/lines/${l.id}/rotate-secret`))
      .then(out => { setSecret({ secret: out.secret, sipUri: out.line.sip_uri }); lines.reload(); })
      .catch(e => toast.error("Could not rotate", errText(e)))
      .finally(() => setBusy(null));
  };
  const remove = (l: VoiceLine) => {
    if (!window.confirm(`Delete ${l.label}? Its extension map is deleted too; the call log stays.`)) return;
    void act(`d${l.id}`, () => api.del(vcApi(slug, `/lines/${l.id}`)), "Line deleted");
  };

  if (lines.error && isMissingRoute(lines.error)) {
    return <Card><EmptyState icon={<LuPhone />} title="Phone lines are not available on this server yet"
      body="The phone module is still being deployed. The setup guide already works, so the PBX can be prepared now."
      action={<Button href={ccHref(slug, "/voice/setup")}>Open the setup guide</Button>} /></Card>;
  }
  return (
    <>
      <div className="cc-spread">
        <p className="cc-sub" style={{ margin: 0, maxWidth: 640 }}>
          Guests pick up the room phone, dial the AI extension and talk to the same concierge. Internal calls only: the AI never dials out.
        </p>
        {manage && <Button icon={<LuPlus />} onClick={() => setEditing(null)}>New line</Button>}
      </div>
      {lines.error && <div className="cc-callout" role="alert">{errText(lines.error)}</div>}
      {lines.loading && !lines.data ? <Skeleton lines={6} /> : !lines.data?.length ? (
        <Card><EmptyState icon={<LuPhone />} title="No phone line yet"
          body="Create a line, then follow the setup guide for your PBX. It stays off until the test call passes."
          action={manage ? <Button onClick={() => setEditing(null)}>New line</Button> : undefined} /></Card>
      ) : lines.data.map(l => (
        <Card key={l.id} title={l.label} subtitle={PBX_LABEL[l.pbx_kind] ?? l.pbx_kind}
          actions={<Badge tone={l.enabled ? "ok" : "neutral"} dot>{l.enabled ? "Live" : "Off"}</Badge>}>
          <div className="cx-stack" style={{ gap: 12 }}>
            <dl className="cc-kv">
              <dt>Dial target</dt>
              <dd>{l.sip_uri ? <span className="cx-row"><span className="cc-mono">{l.sip_uri}</span>
                <Button size="sm" variant="ghost" icon={<LuCopy />} aria-label="Copy dial target" onClick={() => copy(l.sip_uri!)} /></span>
                : <span className="cx-faint">Add the SIP trunk id</span>}</dd>
              <dt>Line secret</dt>
              <dd>{l.has_secret ? <span className="cc-mono">{l.line_secret_hint ? `...${l.line_secret_hint}` : "set"}</span> : <Badge tone="warn">Not set</Badge>}
                {l.allowed_sources.length > 0 && <span className="cc-small cx-muted"> plus {l.allowed_sources.length} allowed source{l.allowed_sources.length > 1 ? "s" : ""}</span>}</dd>
              <dt>Extensions</dt>
              <dd>{l.extensions ?? 0} mapped{l.caller_id_mode === "none" ? " (caller id off: the AI asks for the room)" : ""}</dd>
              <dt>Front desk</dt>
              <dd>{l.front_desk_uri ? <span className="cc-mono">{l.front_desk_uri}</span> : <span className="cx-faint">No transfer target: the AI files a callback request instead</span>}</dd>
              <dt>Limits</dt>
              <dd>{l.max_concurrent} at once, up to {durationText(l.max_call_s)} each, tools over {l.tool_transport === "mcp" ? "MCP" : "the sideband worker"}</dd>
            </dl>
            <div className="cx-row">
              <Button size="sm" variant="secondary" onClick={() => setExts(l)}>Extensions</Button>
              {manage && <>
                <Button size="sm" variant={l.enabled ? "secondary" : "primary"} loading={busy === `e${l.id}`}
                  disabled={!l.enabled && (!l.trunk_id || (!l.has_secret && !l.allowed_sources.length))}
                  onClick={() => void act(`e${l.id}`, () => api.patch(vcApi(slug, `/lines/${l.id}`), { enabled: !l.enabled }), l.enabled ? "Line switched off" : "Line is live")}>
                  {l.enabled ? "Switch off" : "Switch on"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(l)}>Edit</Button>
                <Button size="sm" variant="ghost" loading={busy === `r${l.id}`} onClick={() => rotate(l)}>{l.has_secret ? "Rotate secret" : "Create secret"}</Button>
                <Button size="sm" variant="danger" loading={busy === `d${l.id}`} onClick={() => remove(l)}>Delete</Button>
              </>}
              <Button size="sm" variant="ghost" href={ccHref(slug, "/voice/setup")}>Setup guide</Button>
            </div>
          </div>
        </Card>
      ))}
      {editing !== undefined && (
        <LineModal slug={slug} line={editing} onClose={() => setEditing(undefined)} onSaved={(l, s) => {
          setEditing(undefined);
          lines.reload();
          if (s) setSecret({ secret: s, sipUri: l.sip_uri });
        }} />
      )}
      {secret && <SecretReveal secret={secret.secret} sipUri={secret.sipUri} onClose={() => setSecret(null)} />}
      {exts && <ExtensionsDrawer slug={slug} line={exts} canManage={manage} onClose={() => setExts(null)} onChanged={lines.reload} />}
    </>
  );
}
