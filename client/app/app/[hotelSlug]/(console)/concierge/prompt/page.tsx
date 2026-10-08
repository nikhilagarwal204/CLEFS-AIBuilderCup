"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Prompt preview (GM only: concierge.manage = owner + gm). The exact
   instructions, tools and greeting the server would build for one surface
   and language, with a sample room and no guest's personal details. The
   browser never builds a prompt any more (03 spec 2.1); this page is how a
   GM sees what the model is told, and the prompt_version that every
   session, transcript and eval row carries so a change can be traced.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuCopy, LuScrollText } from "react-icons/lu";
import { hotelPath, useApi, withQuery } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Select, Skeleton, useToast } from "../../../../../ui";
import { LANGUAGE_NAMES, PERM, ccApi, errText } from "../lib";
import type { PromptPreview, Surface, Venue } from "../types";

const SURFACES: { value: Surface; label: string }[] = [
  { value: "web_room", label: "Room QR (voice and text)" },
  { value: "web_location", label: "Venue QR" },
  { value: "phone", label: "In-room phone" },
  { value: "text_fallback", label: "Text chat" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "eval", label: "Evaluation run" },
];

export default function PromptPreviewPage() {
  const { slug, hotel } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const allowed = can(PERM.manage);
  const [surface, setSurface] = useState<Surface>("web_room");
  const [language, setLanguage] = useState("en");
  const [room, setRoom] = useState("101");
  const [location, setLocation] = useState("");
  const venues = useApi<Venue[]>(allowed && surface === "web_location" ? hotelPath(slug, "/locations") : null, [slug, surface]);
  const path = allowed && (surface !== "web_location" || location)
    ? withQuery(ccApi(slug, "/prompt-preview"), {
      surface, language, room: surface === "web_location" ? undefined : room.trim() || undefined,
      location: surface === "web_location" ? location : undefined,
    })
    : null;
  const preview = useApi<PromptPreview>(path, [path]);

  if (!allowed) {
    return <Card><EmptyState icon={<LuScrollText />} title="GM only" body="Only the owner or GM can see the concierge prompt." /></Card>;
  }
  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text).then(() => toast.success("Copied"), () => toast.error("Could not copy"));
  };
  const p = preview.data;
  const langs = hotel?.guest_languages?.length ? hotel.guest_languages : ["en", "ar"];

  return (
    <>
      <Card>
        <div className="cc-fields" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          <Field label="Surface"><Select value={surface} onChange={e => setSurface(e.target.value as Surface)} options={SURFACES} /></Field>
          <Field label="Language"><Select value={language} onChange={e => setLanguage(e.target.value)}
            options={langs.map(l => ({ value: l, label: LANGUAGE_NAMES[l] ?? l }))} /></Field>
          {surface === "web_location" ? (
            <Field label="Venue"><Select value={location} onChange={e => setLocation(e.target.value)} placeholder="Choose a venue"
              options={(venues.data ?? []).map(v => ({ value: v.slug, label: v.name }))} /></Field>
          ) : (
            <Field label="Sample room" hint="No guest details are added."><Input value={room} onChange={e => setRoom(e.target.value.slice(0, 12))} /></Field>
          )}
        </div>
      </Card>
      {preview.error && <div className="cc-callout" role="alert">{errText(preview.error)}</div>}
      {!path ? <p className="cc-sub">Choose a venue to preview its prompt.</p> : preview.loading && !p ? <Skeleton lines={12} /> : p && (
        <>
          <Card title="Session" actions={<Badge tone="gold">Prompt {p.prompt_version}</Badge>}>
            <dl className="cc-kv">
              <dt>Model</dt><dd className="cc-mono">{p.model}</dd>
              <dt>Voice</dt><dd>{p.voice ?? "none (text)"}</dd>
              <dt>Knowledge</dt><dd>{p.kb_mode === "structured" ? `${p.kb_entry_ids.length} approved answers` : "Old free text, unreviewed"}</dd>
              <dt>Greeting</dt><dd dir="auto">{p.greeting_text || "none"}</dd>
            </dl>
          </Card>
          <Card title="Instructions" subtitle={`${p.instructions.length.toLocaleString()} characters`}
            actions={<Button size="sm" variant="ghost" icon={<LuCopy />} onClick={() => copy(p.instructions)}>Copy</Button>}>
            <pre className="cc-pre" dir="auto">{p.instructions}</pre>
          </Card>
          <Card title="Tools" subtitle="What the model may ask the server to do on this surface. The server runs every one; the room always comes from the session.">
            <div className="cx-stack" style={{ gap: 10 }}>
              {p.tools.length === 0 && <p className="cc-sub">No tools.</p>}
              {p.tools.map((t, i) => (
                <details key={t.name ?? i}>
                  <summary><strong className="cc-mono">{t.name ?? t.type}</strong> <span className="cc-small cx-muted">{t.description}</span></summary>
                  <pre className="cc-pre cc-pre--short" style={{ marginTop: 8 }}>{JSON.stringify(t.parameters ?? t, null, 2)}</pre>
                </details>
              ))}
            </div>
          </Card>
        </>
      )}
    </>
  );
}
