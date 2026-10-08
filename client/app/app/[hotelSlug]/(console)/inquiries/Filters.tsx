"use client";

/* List filters (4.11): stage, segment, source, SLA state and check-in dates.
   Multi-selects are chip toggles rather than <select multiple>, which is
   unusable on a phone. Nothing here fetches; the console turns the value
   into the list query (lib.filterQuery). */

import { Button, Field, Input, Segmented } from "../../../../ui";
import {
  activeFilterCount, DEFAULT_FILTERS, EMPTY_FILTERS, SEGMENT_LABEL, SEGMENTS, SOURCE_LABEL, SOURCES, STAGE_LABEL,
  STAGES, type Filters as FilterValue,
} from "./lib";

function Chips<V extends string>({
  label, options, labels, value, onChange,
}: {
  label: string;
  options: readonly V[];
  labels: Record<V, string>;
  value: V[];
  onChange: (v: V[]) => void;
}) {
  const toggle = (o: V) => onChange(value.includes(o) ? value.filter(x => x !== o) : [...value, o]);
  return (
    <div className="inq-chip-group" role="group" aria-label={label}>
      <span className="inq-chip-group__label">{label}</span>
      <div className="inq-chips">
        {options.map(o => (
          <button key={o} type="button" className="inq-chip" aria-pressed={value.includes(o)} onClick={() => toggle(o)}>
            {labels[o]}
          </button>
        ))}
      </div>
    </div>
  );
}

const SLA_OPTIONS = [
  { value: "", label: "Any" },
  { value: "breached", label: "Overdue" },
  { value: "due", label: "Due" },
  { value: "answered", label: "Answered" },
] as const;

export default function Filters({ value, onChange }: { value: FilterValue; onChange: (f: FilterValue) => void }) {
  const set = <K extends keyof FilterValue>(k: K, v: FilterValue[K]) => onChange({ ...value, [k]: v });
  const n = activeFilterCount(value);
  return (
    <div className="inq-filters">
      <Chips label="Stage" options={STAGES} labels={STAGE_LABEL} value={value.stage} onChange={v => set("stage", v)} />
      <Chips label="Segment" options={SEGMENTS} labels={SEGMENT_LABEL} value={value.segment}
        onChange={v => set("segment", v)} />
      <Chips label="Source" options={SOURCES} labels={SOURCE_LABEL} value={value.source}
        onChange={v => set("source", v)} />
      <div className="inq-filters__row">
        <Field label="First reply">
          <Segmented size="sm" ariaLabel="First reply" options={SLA_OPTIONS} value={value.sla}
            onChange={v => set("sla", v)} />
        </Field>
        <Field label="Check-in from">
          <Input type="date" value={value.check_in_from} onChange={e => set("check_in_from", e.target.value)} />
        </Field>
        <Field label="Check-in to">
          <Input type="date" value={value.check_in_to} min={value.check_in_from || undefined}
            onChange={e => set("check_in_to", e.target.value)} />
        </Field>
        <span className="inq-spacer" />
        <Button size="sm" variant="ghost" onClick={() => onChange(DEFAULT_FILTERS)}>Open only</Button>
        <Button size="sm" variant="ghost" disabled={n === 0} onClick={() => onChange(EMPTY_FILTERS)}>
          Show all
        </Button>
      </div>
    </div>
  );
}
