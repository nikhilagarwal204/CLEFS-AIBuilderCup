"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One knowledge answer, English and Arabic side by side. The Arabic
   controls are dir="rtl" themselves (not just the column) so the caret,
   punctuation and selection behave for an Arabic typist.

   Saving never publishes by itself: a new answer, or an edit to a live one,
   goes back to "waiting for approval" (the server bumps the version) unless
   the manager presses "Save and approve". Only approved answers reach
   guests, which is the whole point of the structured knowledge base.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Drawer, Field, Input, Select, Textarea, useToast } from "../../../../../ui";
import {
  FACT_KEYS, KIND_OPTIONS, PERM, STATUS_LABEL, STATUS_TONE, ccApi, draftBody, draftDiff, draftFrom, emptyDraft, errText,
} from "../lib";
import type { KbEntry, KbEntryDraft, KbSection } from "../types";
import { Revisions } from "./Revisions";
import { TestAnswer } from "./TestAnswer";

export interface EntryEditorProps {
  slug: string;
  open: boolean;
  /** null = a new answer. */
  entry: KbEntry | null;
  sections: KbSection[];
  defaultSectionId: number | null;
  onClose: () => void;
  onChanged: (entry: KbEntry) => void;
}

export function EntryEditor(props: EntryEditorProps) {
  // Keyed remount: a different entry (or a fresh "new") starts a clean
  // draft without an effect that copies props into state.
  const key = props.entry ? `e${props.entry.id}v${props.entry.version}${props.entry.status}` : `new${props.defaultSectionId}`;
  return <Drawer open={props.open} onClose={props.onClose} wide
    title={props.entry ? "Edit answer" : "New answer"}>
    {props.open && <EditorBody key={key} {...props} />}
  </Drawer>;
}

function EditorBody({ slug, entry, sections, defaultSectionId, onClose, onChanged }: EntryEditorProps) {
  const { can } = useSession();
  const toast = useToast();
  const manage = can(PERM.manage);
  const initial = entry ? draftFrom(entry) : emptyDraft(defaultSectionId);
  const [d, setD] = useState<KbEntryDraft>(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const set = <K extends keyof KbEntryDraft>(k: K, v: KbEntryDraft[K]) => setD(prev => ({ ...prev, [k]: v }));
  const dirty = JSON.stringify(draftBody(initial)) !== JSON.stringify(draftBody(d));

  async function run(label: string, fn: () => Promise<KbEntry>, done: string) {
    setBusy(label);
    setErr(null);
    try {
      const saved = await fn();
      onChanged(saved);
      toast.success(done);
      onClose();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(null);
    }
  }

  const save = (approve: boolean) => {
    if (!d.answer_en.trim()) { setErr("Write the English answer first."); return; }
    if (d.kind === "local_place" && !d.source_url.trim()) {
      setErr("A local place needs a source link, so guests are only told about places you can vouch for.");
      return;
    }
    const path = ccApi(slug, "/kb/entries");
    return run(approve ? "approve" : "save",
      () => entry
        ? api.patch<KbEntry>(`${path}/${entry.id}`, { ...draftDiff(draftFrom(entry), d), approve })
        : api.post<KbEntry>(path, { ...draftBody(d), approve }),
      approve ? "Saved and live for guests" : "Saved. It waits for approval before guests hear it.");
  };
  const act = (verb: "approve" | "reject" | "archive", done: string) =>
    entry && run(verb, () => api.post<KbEntry>(ccApi(slug, `/kb/entries/${entry.id}/${verb}`), verb === "reject" ? {} : undefined), done);

  const fact = FACT_KEYS.find(f => f.value === d.fact_key);
  const ro = !manage;
  return (
    <div className="cx-stack">
      {entry && (
        <div className="cx-row">
          <Badge tone={STATUS_TONE[entry.status]} dot>{STATUS_LABEL[entry.status]}</Badge>
          <span className="cc-small cx-muted">Version {entry.version}</span>
          {entry.rejected_reason && <span className="cc-small cx-muted">Rejected: {entry.rejected_reason}</span>}
        </div>
      )}
      <div className="cc-fields">
        <Field label="Section">
          <Select value={d.section_id ?? ""} disabled={ro}
            onChange={e => set("section_id", e.target.value ? Number(e.target.value) : null)}
            options={[{ value: "", label: "No section" }, ...sections.map(s => ({ value: String(s.id), label: s.title_en }))]} />
        </Field>
        <Field label="Kind" hint={d.kind === "venue_note" ? "Shown to the concierge only on that venue's QR code." : undefined}>
          <Select value={d.kind} disabled={ro} onChange={e => set("kind", e.target.value as KbEntryDraft["kind"])} options={KIND_OPTIONS} />
        </Field>
      </div>

      <div className="cc-bilingual">
        <div className="cc-bilingual__col">
          <span className="cc-label">English</span>
          <Field label="Guest question" hint="How a guest would ask it. Optional for a plain fact.">
            <Input value={d.question_en} readOnly={ro} onChange={e => set("question_en", e.target.value)} placeholder="What time is breakfast?" />
          </Field>
          <Field label="Answer" required>
            <Textarea rows={5} value={d.answer_en} readOnly={ro} onChange={e => set("answer_en", e.target.value)}
              placeholder="Breakfast is served in Saffron on the ground floor from 6:30 to 10:30." />
          </Field>
        </div>
        <div className="cc-bilingual__col" lang="ar">
          <span className="cc-label">العربية (Arabic)</span>
          <Field label="سؤال الضيف (guest question)">
            <Input dir="rtl" className="cc-rtl" value={d.question_ar} readOnly={ro} onChange={e => set("question_ar", e.target.value)} />
          </Field>
          <Field label="الإجابة (answer)" hint="Leave blank and the concierge translates the English answer itself.">
            <Textarea dir="rtl" className="cc-rtl" rows={5} value={d.answer_ar} readOnly={ro} onChange={e => set("answer_ar", e.target.value)} />
          </Field>
        </div>
      </div>

      <div className="cc-fields">
        {d.kind === "fact" && (
          <>
            <Field label="Structured fact" hint="Lets Visibility and the website read this fact too.">
              <Select value={d.fact_key} disabled={ro} onChange={e => set("fact_key", e.target.value)}
                options={[{ value: "", label: "None" }, ...FACT_KEYS.map(f => ({ value: f.value, label: f.label }))]} />
            </Field>
            {d.fact_key && (
              <Field label="Value" hint={fact?.hint}>
                <Input value={d.fact_value} readOnly={ro} onChange={e => set("fact_value", e.target.value)} />
              </Field>
            )}
          </>
        )}
        {d.kind === "local_place" && (
          <>
            <Field label="Source link" required hint="Where this place's details come from (its site or map listing).">
              <Input type="url" value={d.source_url} readOnly={ro} onChange={e => set("source_url", e.target.value)} placeholder="https://" />
            </Field>
            <Field label="Source name">
              <Input value={d.source_label} readOnly={ro} onChange={e => set("source_label", e.target.value)} placeholder="Official website" />
            </Field>
          </>
        )}
        {d.kind === "venue_note" && (
          <Field label="Venue" hint="The venue's short code, from the Venues tab.">
            <Input value={d.location_slug} readOnly={ro} onChange={e => set("location_slug", e.target.value)} placeholder="pool-bar" />
          </Field>
        )}
        <Field label="Valid from" hint="Seasonal answers switch themselves on and off.">
          <Input type="date" value={d.valid_from} readOnly={ro} onChange={e => set("valid_from", e.target.value)} />
        </Field>
        <Field label="Valid until">
          <Input type="date" value={d.valid_to} readOnly={ro} onChange={e => set("valid_to", e.target.value)} />
        </Field>
        <Field label="Tags" className="cc-fields__wide" hint="Comma separated, for search.">
          <Input value={d.tags.join(", ")} readOnly={ro}
            onChange={e => set("tags", e.target.value.split(",").map(t => t.trim()).filter(Boolean))} />
        </Field>
      </div>

      {err && <div className="cc-callout" role="alert">{err}</div>}

      {manage && (
        <div className="cx-row">
          <Button loading={busy === "approve"} disabled={!!busy} onClick={() => save(true)}>Save and approve</Button>
          <Button variant="secondary" loading={busy === "save"} disabled={!!busy || (!!entry && !dirty)} onClick={() => save(false)}>
            {entry ? "Save changes" : "Save for approval"}
          </Button>
          {entry?.status === "pending" && !dirty && (
            <Button variant="secondary" disabled={!!busy} onClick={() => act("approve", "Approved. Guests hear it from now on.")}>Approve as is</Button>
          )}
          {entry?.status === "pending" && (
            <Button variant="ghost" disabled={!!busy} onClick={() => act("reject", "Rejected")}>Reject</Button>
          )}
          {entry && entry.status !== "archived" && (
            <Button variant="danger" disabled={!!busy} onClick={() => act("archive", "Withdrawn from guests")}>Withdraw</Button>
          )}
        </div>
      )}

      <TestAnswer slug={slug} defaultQuestion={d.question_en || d.answer_en.slice(0, 80)} entryId={entry?.id ?? null} />
      {entry && <Revisions slug={slug} entryId={entry.id} version={entry.version} />}
    </div>
  );
}
