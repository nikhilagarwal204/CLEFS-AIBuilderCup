"use client";

/* Answer one grouped guest question. The question is prefilled from the
   group's title; the Arabic pair is optional (the concierge translates the
   English answer when it is blank) and typed right to left. "Approve now"
   is on by default because a manager answering here is the approver. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { ccApi, errText } from "../lib";
import type { KbEntry, KbSection, KbUnknown } from "../types";

export function AnswerModal({ slug, unknown, sections, onClose, onDone }: {
  slug: string;
  unknown: KbUnknown;
  sections: KbSection[];
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [qEn, setQEn] = useState(unknown.title);
  const [aEn, setAEn] = useState("");
  const [qAr, setQAr] = useState("");
  const [aAr, setAAr] = useState("");
  const [section, setSection] = useState("");
  const [approve, setApprove] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!aEn.trim()) { setErr("Write the English answer first."); return; }
    setBusy(true);
    setErr(null);
    try {
      await api.post<KbEntry>(ccApi(slug, `/kb/unknowns/${unknown.id}/answer`), {
        question_en: qEn.trim() || null, answer_en: aEn.trim(),
        question_ar: qAr.trim() || null, answer_ar: aAr.trim() || null,
        section_key: section || null, approve,
      });
      toast.success(approve ? "Answered. Guests hear it from now on." : "Saved for approval in Knowledge.");
      onDone();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="Answer this question" size="lg" footer={
      <div className="cx-row">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button loading={busy} onClick={() => void submit()}>{approve ? "Save and approve" : "Save for approval"}</Button>
      </div>
    }>
      <div className="cx-stack">
        <div className="cc-bilingual">
          <div className="cc-bilingual__col">
            <span className="cc-label">English</span>
            <Field label="Question"><Input value={qEn} onChange={e => setQEn(e.target.value)} /></Field>
            <Field label="Answer" required><Textarea rows={4} value={aEn} onChange={e => setAEn(e.target.value)} /></Field>
          </div>
          <div className="cc-bilingual__col" lang="ar">
            <span className="cc-label">العربية (Arabic, optional)</span>
            <Field label="السؤال (question)"><Input dir="rtl" className="cc-rtl" value={qAr} onChange={e => setQAr(e.target.value)} /></Field>
            <Field label="الإجابة (answer)"><Textarea dir="rtl" className="cc-rtl" rows={4} value={aAr} onChange={e => setAAr(e.target.value)} /></Field>
          </div>
        </div>
        <div className="cc-fields">
          <Field label="Section">
            <Select value={section} onChange={e => setSection(e.target.value)}
              options={[{ value: "", label: "Let Clefs choose" }, ...sections.map(s => ({ value: s.key, label: s.title_en }))]} />
          </Field>
          <label className="cc-check" style={{ alignSelf: "end" }}>
            <input type="checkbox" checked={approve} onChange={e => setApprove(e.target.checked)} />
            <span>Approve now, so the concierge uses it straight away</span>
          </label>
        </div>
        {err && <div className="cc-callout" role="alert">{err}</div>}
      </div>
    </Modal>
  );
}
