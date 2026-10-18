"use client";

/* Quick-reply chips: the buttons under the concierge on the guest page
   ("Extra towels", "Late checkout"), per language. A language with no chips
   of its own falls back to English on the guest page. The label is what the
   guest taps and what the concierge receives as the guest's words, so it is
   written as the guest would say it; the key is stable for analytics. Arabic
   labels are typed right to left. */

import { useState } from "react";
import { LuPlus, LuTrash2 } from "react-icons/lu";
import { Button, Card, Input, Segmented } from "../../../../../ui";
import { LANGUAGE_NAMES } from "../lib";
import type { Chip } from "../types";

const MAX_CHIPS = 8;
const RTL = new Set(["ar", "ur"]);

function keyOf(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}

export function ChipsCard({ languages, chips, disabled, saving, onSave }: {
  languages: string[];
  chips: Record<string, Chip[]>;
  disabled: boolean;
  saving: boolean;
  onSave: (chips: Record<string, Chip[]>) => void;
}) {
  const langs = languages.length ? languages : ["en"];
  const [lang, setLang] = useState(langs[0]);
  const [draft, setDraft] = useState<Record<string, Chip[]>>(chips);
  const rows = draft[lang] ?? [];
  const rtl = RTL.has(lang);
  const dirty = JSON.stringify(draft) !== JSON.stringify(chips);

  const setRows = (next: Chip[]) => setDraft(prev => ({ ...prev, [lang]: next }));
  const update = (i: number, patch: Partial<Chip>) => setRows(rows.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  function save() {
    // Drop blank rows and give label-only rows a key (English rows derive
    // it from the label; others reuse the English key at the same position).
    const clean: Record<string, Chip[]> = {};
    for (const [l, list] of Object.entries(draft)) {
      const kept = list.filter(c => c.label.trim()).map((c, i) => ({
        key: c.key.trim() || keyOf(c.label) || draft.en?.[i]?.key || `chip_${i + 1}`,
        label: c.label.trim().slice(0, 60),
      }));
      if (kept.length) clean[l] = kept;
    }
    onSave(clean);
  }

  return (
    <Card title="Quick-reply chips" subtitle="The buttons guests tap under the concierge. Up to 8 per language; a language without its own uses English.">
      <div className="cx-stack">
        <Segmented size="sm" ariaLabel="Chip language" value={lang} onChange={setLang}
          options={langs.map(l => ({ value: l, label: `${LANGUAGE_NAMES[l] ?? l}${draft[l]?.length ? ` (${draft[l].length})` : ""}` }))} />
        <div className="cc-chips">
          {rows.length === 0 && <p className="cc-sub">No chips in {LANGUAGE_NAMES[lang] ?? lang}{lang !== "en" ? "; guests see the English ones." : "."}</p>}
          {rows.map((c, i) => (
            <div key={i} className="cc-chip-row">
              <Input aria-label="Key" className="cc-mono" value={c.key} disabled={disabled} placeholder="towels"
                onChange={e => update(i, { key: e.target.value.replace(/[^a-z0-9_]/gi, "").toLowerCase().slice(0, 40) })} />
              <Input aria-label="Label" value={c.label} disabled={disabled} dir={rtl ? "rtl" : "ltr"} className={rtl ? "cc-rtl" : undefined}
                placeholder={rtl ? "" : "Extra towels please"} onChange={e => update(i, { label: e.target.value })} />
              <Button size="sm" variant="ghost" aria-label="Remove chip" icon={<LuTrash2 />} disabled={disabled}
                onClick={() => setRows(rows.filter((_, j) => j !== i))} />
            </div>
          ))}
        </div>
        <div className="cx-row">
          <Button size="sm" variant="secondary" icon={<LuPlus />} disabled={disabled || rows.length >= MAX_CHIPS}
            onClick={() => setRows([...rows, { key: "", label: "" }])}>Add chip</Button>
          <Button size="sm" loading={saving} disabled={disabled || !dirty} onClick={save}>Save chips</Button>
        </div>
      </div>
    </Card>
  );
}
