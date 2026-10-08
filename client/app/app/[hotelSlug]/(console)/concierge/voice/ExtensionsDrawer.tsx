"use client";

/* The extension allowlist of one line: which internal extension is which
   room (or venue). The caller id a PBX sends is untrusted on its own; only
   an extension on this list binds a call to a room, so a request filed from
   it is "verified". An unlisted extension still reaches the AI, which asks
   for the room and marks the request for staff to confirm at the door. */

import { useState } from "react";
import { LuTrash2 } from "react-icons/lu";
import { api } from "../../../../../lib/api";
import { Badge, Button, Drawer, Field, Input, Select, Skeleton, Table, useToast, type Column } from "../../../../../ui";
import { errText, vcApi } from "../lib";
import type { VoiceExtension, VoiceLine } from "../types";
import { ExtensionImport } from "./ExtensionImport";
import { useVoiceApi } from "./voiceApi";

const SOURCE_LABEL: Record<string, string> = { manual: "Added by hand", csv: "CSV", rule: "Rule" };

export function ExtensionsDrawer({ slug, line, canManage, onClose, onChanged }: {
  slug: string;
  line: VoiceLine;
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const path = vcApi(slug, `/lines/${line.id}/extensions`);
  const list = useVoiceApi<VoiceExtension[]>(path, "extensions", [path]);
  const [ext, setExt] = useState("");
  const [kind, setKind] = useState<"room" | "location">("room");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => { list.reload(); onChanged(); };

  async function add() {
    setBusy(true);
    setErr(null);
    try {
      await api.post(path, { extension: ext.trim(), kind, room: kind === "room" ? target.trim() : null, location: kind === "location" ? target.trim() : null });
      setExt("");
      setTarget("");
      refresh();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(x: VoiceExtension) {
    try {
      await api.del(`${path}/${x.id}`);
      refresh();
    } catch (e) {
      toast.error("Could not remove", errText(e));
    }
  }

  const columns: Column<VoiceExtension>[] = [
    { key: "ext", header: "Extension", render: x => <span className="cc-mono">{x.extension}</span> },
    { key: "to", header: "Reaches", render: x => x.room ? `Room ${x.room}` : x.location_slug ? `Venue ${x.location_slug}` : x.kind },
    { key: "src", header: "From", hideOnMobile: true, render: x => <Badge>{SOURCE_LABEL[x.source] ?? x.source}</Badge> },
    ...(canManage ? [{ key: "rm", header: "", align: "right" as const, render: (x: VoiceExtension) => (
      <Button size="sm" variant="ghost" aria-label={`Remove ${x.extension}`} icon={<LuTrash2 />} onClick={() => void remove(x)} />
    ) }] : []),
  ];

  return (
    <Drawer open onClose={onClose} wide title={`Extensions on ${line.label}`}>
      <div className="cx-stack">
        {canManage && (
          <div className="cx-stack" style={{ gap: 10 }}>
            <span className="cc-label">Add one</span>
            <div className="cc-fields" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
              <Field label="Extension"><Input className="cc-mono" value={ext} onChange={e => setExt(e.target.value.slice(0, 32))} placeholder="2504" /></Field>
              <Field label="Reaches"><Select value={kind} onChange={e => setKind(e.target.value as "room" | "location")}
                options={[{ value: "room", label: "A room" }, { value: "location", label: "A venue" }]} /></Field>
              <Field label={kind === "room" ? "Room number" : "Venue code"}>
                <Input value={target} onChange={e => setTarget(e.target.value.slice(0, 60))} placeholder={kind === "room" ? "504" : "pool-bar"} />
              </Field>
            </div>
            <div><Button size="sm" loading={busy} disabled={!ext.trim() || !target.trim()} onClick={() => void add()}>Add</Button></div>
            {err && <div className="cc-callout" role="alert">{err}</div>}
            <ExtensionImport slug={slug} line={line} onDone={refresh} />
          </div>
        )}
        <span className="cc-label">{list.data ? `${list.data.length} mapped` : "Mapped"}</span>
        {list.error && <div className="cc-callout" role="alert">{errText(list.error)}</div>}
        {list.loading && !list.data ? <Skeleton lines={5} /> : (
          <Table columns={columns} rows={list.data ?? []} rowKey={x => x.id} caption="Extensions"
            empty="No extensions yet. Every call will be asked for its room." />
        )}
      </div>
    </Drawer>
  );
}
