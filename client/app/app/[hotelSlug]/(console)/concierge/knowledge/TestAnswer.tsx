"use client";

/* "Test this answer": ask the concierge one question exactly as a guest
   would, against the knowledge that is live right now, and see its reply.
   Runs one dry-run evaluation on the server (surface "eval": nothing is
   filed, no order placed, no turn logged), so an owner can check an answer
   before approving it without walking to a room with a phone.

   The route (POST /hotels/{slug}/concierge/kb/test) belongs to the
   concierge server; until it is deployed the panel says so instead of
   failing, and points to Trust's full evaluation suite. */

import { useState } from "react";
import { LuFlaskConical } from "react-icons/lu";
import { api } from "../../../../../lib/api";
import { useSession } from "../../../../../lib/session";
import { Button, Field, Input, Segmented } from "../../../../../ui";
import { PERM, ccApi, errText, isMissingRoute } from "../lib";
import type { KbTestResult } from "../types";

export function TestAnswer({ slug, defaultQuestion, entryId }: { slug: string; defaultQuestion: string; entryId: number | null }) {
  const { can } = useSession();
  const [q, setQ] = useState(defaultQuestion);
  const [lang, setLang] = useState<"en" | "ar">("en");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<KbTestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!can(PERM.manage)) return null;

  async function ask() {
    if (!q.trim()) return;
    setBusy(true);
    setErr(null);
    setRes(null);
    try {
      setRes(await api.post<KbTestResult>(ccApi(slug, "/kb/test"), { question: q.trim(), language: lang, entry_id: entryId }));
    } catch (e) {
      setErr(isMissingRoute(e)
        ? "Single-question tests are not switched on for this server yet. Run the full suite from Trust, AI evaluations."
        : errText(e));
    } finally {
      setBusy(false);
    }
  }

  const cited = res && entryId != null && (res.cited ?? res.kb_entry_ids ?? []).includes(entryId);
  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      <div className="cc-spread">
        <span className="cc-label">Test this answer</span>
        <Segmented size="sm" ariaLabel="Test language" value={lang} onChange={setLang}
          options={[{ value: "en", label: "English" }, { value: "ar", label: "العربية" }]} />
      </div>
      <p className="cc-sub" style={{ marginTop: 0 }}>
        Uses the answers that are live now. An unapproved edit is not used until you approve it.
      </p>
      <div className="cx-row" style={{ alignItems: "flex-end" }}>
        <Field label="Guest asks" className="cc-grow">
          <Input value={q} dir={lang === "ar" ? "rtl" : "ltr"} onChange={e => setQ(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") void ask(); }} />
        </Field>
        <Button variant="secondary" icon={<LuFlaskConical />} loading={busy} onClick={ask}>Ask</Button>
      </div>
      {err && <div className="cc-callout cc-callout--info">{err}</div>}
      {res && (
        <div className="cx-stack" style={{ gap: 6 }}>
          <div className="cc-bubble cc-bubble--ai" dir={lang === "ar" ? "rtl" : "ltr"}>{res.text || "(no reply)"}</div>
          <span className="cc-small cx-muted">
            {entryId != null && (cited ? "It used this answer." : "It did not use this answer.")}
            {res.tool_calls?.length ? ` Would call: ${res.tool_calls.map(t => t.name).join(", ")}.` : ""}
            {res.prompt_version ? ` Prompt ${res.prompt_version}.` : ""}
          </span>
        </div>
      )}
    </div>
  );
}
