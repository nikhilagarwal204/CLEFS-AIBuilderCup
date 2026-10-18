"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Transcripts: what guests said to the concierge and what it answered,
   one row per conversation (GET /concierge/transcripts). Masking happens
   on the server by role (03 spec 5.2, pii class "transcript"): owner, GM
   and front desk read the words; any other role that can open this page
   sees when conversations happened and how long they were, never the
   words. The page never decides that itself, it renders what it got.

   Only turns logged with the guest's recording consent exist at all, and
   Trust deletes them after 12 months. The list preview shortens phone
   numbers and emails even for full readers (server-side); the whole text
   shows only inside one conversation. Search is by room because that is
   how a complaint arrives ("room 504 says the concierge promised a late
   checkout"); "Flagged only" narrows to emergencies, sensitive topics and
   hand-offs.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuMessageSquareDashed, LuSearch } from "react-icons/lu";
import { apiFetch, hotelPath, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, EmptyState, Field, Input, Select, Skeleton, Table, useToast, type Column } from "../../../../../ui";
import { errText } from "../lib";
import type { TranscriptPage, TranscriptSource, TranscriptSummary } from "../types";
import { SAFETY_TONE, SOURCE_LABEL, SOURCE_OPTIONS } from "./labels";
import { TranscriptDrawer } from "./TranscriptDrawer";

interface Query { room: string; source: TranscriptSource | ""; flagged: boolean }

const PAGE = 50;

function listPath(slug: string, q: Query, before?: string): string {
  return withQuery(hotelPath(slug, "/concierge/transcripts"), {
    room: q.room || undefined, source: q.source || undefined, flagged: q.flagged ? "true" : undefined, limit: PAGE, before,
  });
}

export default function TranscriptsPage() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const [room, setRoom] = useState("");
  const [query, setQuery] = useState<Query>({ room: "", source: "", flagged: false });
  // Pages after the first, fetched on "Show older"; reset whenever the query changes.
  const [more, setMore] = useState<{ rows: TranscriptSummary[]; next: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [open, setOpen] = useState<TranscriptSummary | null>(null);
  const first = useApi<TranscriptPage>(listPath(slug, query), [slug, query]);

  const apply = (next: Partial<Query>) => { setMore(null); setQuery(q => ({ ...q, ...next })); };
  const rows = [...(first.data?.conversations ?? []), ...(more?.rows ?? [])];
  const cursor = more ? more.next : first.data?.next_before ?? null;
  const hidden = first.data?.visibility === "none";

  const loadOlder = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await apiFetch<TranscriptPage>(listPath(slug, query, cursor));
      setMore(m => ({ rows: [...(m?.rows ?? []), ...page.conversations], next: page.next_before }));
    } catch (err) {
      toast({ title: "Could not load older conversations", body: errText(err), tone: "bad" });
    } finally {
      setLoadingMore(false);
    }
  };

  const columns: Column<TranscriptSummary>[] = [
    { key: "when", header: "When", render: c => fmt.dateTime(c.last_at) },
    { key: "room", header: "Room or venue", render: c => c.room ?? "unknown" },
    { key: "source", header: "Channel", hideOnMobile: true, render: c => SOURCE_LABEL[c.source] ?? c.source },
    { key: "turns", header: "Turns", align: "right", render: c => c.turns },
    { key: "flag", header: "Flag", render: c => c.safety_level === "none" ? null
      : <Badge tone={SAFETY_TONE[c.safety_level]} dot>{c.safety_level}</Badge> },
    { key: "preview", header: "Guest said", hideOnMobile: true, render: c =>
      c.preview != null ? <span dir="auto">{c.preview.slice(0, 90)}</span> : <span className="cc-hidden">{hidden ? "Hidden for your role" : ""}</span> },
  ];

  return (
    <>
      <Card>
        <form className="cc-filters" onSubmit={e => { e.preventDefault(); apply({ room: room.trim() }); }}>
          <Field label="Room or venue code" className="cc-grow">
            <Input value={room} onChange={e => setRoom(e.target.value.slice(0, 40))} placeholder="504" />
          </Field>
          <Field label="Channel">
            <Select value={query.source} placeholder="All channels" options={SOURCE_OPTIONS}
              onChange={e => apply({ source: e.target.value as TranscriptSource | "" })} />
          </Field>
          <label className="cc-flag">
            <input type="checkbox" checked={query.flagged} onChange={e => apply({ flagged: e.target.checked })} />
            Flagged only
          </label>
          <Button type="submit" icon={<LuSearch />}>Find</Button>
          {(query.room || query.source || query.flagged) && (
            <Button type="button" variant="ghost" onClick={() => { setRoom(""); apply({ room: "", source: "", flagged: false }); }}>Clear</Button>
          )}
        </form>
        <p className="cc-sub">Only conversations where the guest agreed to recording are kept, for 12 months. Newest first.</p>
      </Card>
      {hidden && <div className="cc-callout cc-callout--info">Your role can see when conversations happened, but not what was said.</div>}
      {first.error && <div className="cc-callout" role="alert">{errText(first.error)}</div>}
      {first.loading && !first.data ? <Skeleton lines={6} /> : first.data && rows.length === 0 ? (
        <Card><EmptyState icon={<LuMessageSquareDashed />} title="No conversations"
          body={query.room ? `Nothing recorded for ${query.room}.` : "Nothing recorded yet."} /></Card>
      ) : rows.length > 0 && (
        <Card padded={false}>
          <Table columns={columns} rows={rows} rowKey={c => c.id} onRowClick={setOpen} caption="Conversations" />
          {cursor && (
            <div className="cc-more">
              <Button variant="secondary" size="sm" loading={loadingMore} onClick={() => void loadOlder()}>Show older</Button>
            </div>
          )}
        </Card>
      )}
      <TranscriptDrawer slug={slug} conv={open} onClose={() => setOpen(null)} />
    </>
  );
}
