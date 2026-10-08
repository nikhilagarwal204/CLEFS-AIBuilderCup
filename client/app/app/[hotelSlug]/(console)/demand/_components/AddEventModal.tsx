"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Manual event entry: POST /events. A manual event is hotel-scoped; when
   it matches a known event it attaches as corroboration (the answer says
   "matched") and never edits the shared record. Checks mirror the server
   (model.validateManualEvent) so common mistakes never round-trip.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { ApiError, api } from "../../../../../lib/api";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { CATEGORY_LABEL, MANUAL_CATEGORIES, manualEventBody, validateManualEvent, type ManualEventForm } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { DemandEvent } from "../_lib/types";

const EMPTY: ManualEventForm = {
  title: "", category: "CONFERENCE", start: "", end: "", venue_text: "", attendance: "", url: "", description: "",
};

export default function AddEventModal({
  slug, open, onClose, onSaved,
}: {
  slug: string;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [f, setF] = useState<ManualEventForm>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof ManualEventForm>(k: K, v: ManualEventForm[K]) => setF(prev => ({ ...prev, [k]: v }));

  const save = async () => {
    const errs = validateManualEvent(f);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const r = await api.post<DemandEvent & { created: boolean; matched: boolean }>(drApi(slug, "/events"), manualEventBody(f));
      toast.success(
        r.matched ? "Matched a known event" : "Event added",
        r.matched ? `Your entry now backs up "${r.title}".` : "It is scored for your hotel in the next recompute.",
      );
      setF(EMPTY);
      onSaved();
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) setErrors(err.fieldErrors);
      toast.error("Could not add the event", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!busy}
      title="Add an event"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={save}>Add event</Button>
        </>
      }
    >
      <div className="cx-stack" style={{ gap: 12 }}>
        <Field label="Name" required error={errors.title}>
          <Input value={f.title} maxLength={300} onChange={e => set("title", e.target.value)} invalid={Boolean(errors.title)}
                 placeholder="e.g. Arab Health, a wedding at the ballroom next door" />
        </Field>
        <Field label="Kind">
          <Select value={f.category} onChange={e => set("category", e.target.value as ManualEventForm["category"])}>
            {MANUAL_CATEGORIES.map(c => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
          </Select>
        </Field>
        <div className="cx-grid cx-grid--2">
          <Field label="First day" required error={errors.start}>
            <Input type="date" value={f.start} onChange={e => set("start", e.target.value)} invalid={Boolean(errors.start)} />
          </Field>
          <Field label="Last day" hint="Leave blank for one day" error={errors.end}>
            <Input type="date" value={f.end} min={f.start || undefined} onChange={e => set("end", e.target.value)} invalid={Boolean(errors.end)} />
          </Field>
        </div>
        <div className="cx-grid cx-grid--2">
          <Field label="Venue" hint="DWTC, Expo City, or a name">
            <Input value={f.venue_text} maxLength={300} onChange={e => set("venue_text", e.target.value)} />
          </Field>
          <Field label="Expected people" error={errors.attendance}>
            <Input inputMode="numeric" value={f.attendance} onChange={e => set("attendance", e.target.value)} invalid={Boolean(errors.attendance)} />
          </Field>
        </div>
        <Field label="Link" error={errors.url}>
          <Input type="url" value={f.url} onChange={e => set("url", e.target.value)} invalid={Boolean(errors.url)} placeholder="https://" />
        </Field>
        <Field label="Notes">
          <Textarea rows={2} maxLength={4000} value={f.description} onChange={e => set("description", e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
