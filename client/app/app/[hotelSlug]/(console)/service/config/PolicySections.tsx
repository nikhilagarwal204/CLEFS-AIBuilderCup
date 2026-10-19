"use client";

/* SLA policies and the compensation authority matrix. Money is edited in
   major units and stored in minor units (Canon 2); a blank limit is
   "unlimited" (the GM row). */

import { useFormat } from "../../../../../lib/format";
import { currencyDigits } from "../../../../../lib/format";
import type { CompensationRule, SlaPolicy } from "../_components/configApi";
import type { SectionState } from "./useSection";
import { SectionFrame } from "./Sections";
import { DEFAULT_LADDER, GESTURE_KINDS, ROLE_LABELS, SCOPE_LABELS } from "./seeds";

// ─── SLA policies ───

const SCOPES = Object.keys(SCOPE_LABELS) as SlaPolicy["scope_type"][];

export function SlaSection({ state }: { state: SectionState<SlaPolicy> }) {
  const ro = state.offline;
  return (
    <SectionFrame title="Response targets" state={state}
                  subtitle={<>Minutes to acknowledge and to finish. The most specific rule wins: request type, then
                    department and level, department, level, hotel default. Escalation when nobody acks:{" "}
                    {DEFAULT_LADDER.map(s => `${ROLE_LABELS[s.to]} at ${s.after_min}m`).join(", ")}.</>}
                  onAdd={() => state.addRow({ scope_type: "request_type", scope_key: "", ack_min: 5, resolve_min: 30, active: true })}>
      <table className="svc-table">
        <thead><tr><th>Applies to</th><th>Key</th><th>Ack (min)</th><th>Finish (min)</th><th>Due by (local)</th><th>Active</th><th /></tr></thead>
        <tbody>
          {state.rows.map((p, i) => (
            <tr key={`${p.scope_type}-${p.scope_key}-${i}`}>
              <td>
                <select aria-label="Applies to" value={p.scope_type} disabled={ro}
                        onChange={e => state.setRow(i, { scope_type: e.target.value as SlaPolicy["scope_type"] })}>
                  {SCOPES.map(s => <option key={s} value={s}>{SCOPE_LABELS[s]}</option>)}
                </select>
              </td>
              <td><input aria-label="Key" value={p.scope_key} disabled={ro || p.scope_type === "default"}
                         placeholder={p.scope_type === "department_severity" ? "housekeeping:L2" : ""}
                         onChange={e => state.setRow(i, { scope_key: e.target.value })} /></td>
              <td><input aria-label="Ack minutes" type="number" min={0} value={p.ack_min} disabled={ro} style={{ width: 80 }}
                         onChange={e => state.setRow(i, { ack_min: Math.max(0, Number(e.target.value)) })} /></td>
              <td><input aria-label="Finish minutes" type="number" min={1} value={p.resolve_min} disabled={ro} style={{ width: 80 }}
                         onChange={e => state.setRow(i, { resolve_min: Math.max(1, Number(e.target.value)) })} /></td>
              <td><input aria-label="Due by local time" type="time" value={p.due_local?.slice(0, 5) ?? ""} disabled={ro}
                         onChange={e => state.setRow(i, { due_local: e.target.value || null })} /></td>
              <td><input aria-label="Active" type="checkbox" checked={p.active !== false} disabled={ro}
                         onChange={e => state.setRow(i, { active: e.target.checked })} /></td>
              <td>{!ro && <button type="button" className="svc-linkbtn" onClick={() => state.removeRow(i)}>Remove</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionFrame>
  );
}

// ─── Compensation authority ───

export function CompensationSection({ state }: { state: SectionState<CompensationRule> }) {
  const hf = useFormat();
  const ro = state.offline;
  return (
    <SectionFrame title="Service recovery authority" state={state}
                  subtitle="What each role may give a guest without asking. Above the limit, the gesture goes to the next role up for approval.">
      <table className="svc-table">
        <thead><tr><th>Role</th><th>Limit per case</th><th>Allowed gestures</th></tr></thead>
        <tbody>
          {state.rows.map((r, i) => {
            const digits = currencyDigits(r.currency || hf.currency);
            const major = r.max_minor === null ? "" : String(r.max_minor / 10 ** digits);
            return (
              <tr key={r.role_key}>
                <td>{ROLE_LABELS[r.role_key] ?? r.role_key}</td>
                <td>
                  <span className="svc-row" style={{ flexWrap: "nowrap" }}>
                    <input aria-label="Limit" type="number" min={0} value={major} placeholder="No limit" disabled={ro} style={{ width: 110 }}
                           onChange={e => state.setRow(i, {
                             max_minor: e.target.value === "" ? null : Math.round(Number(e.target.value) * 10 ** digits),
                           })} />
                    <span className="svc-faint">{r.currency || hf.currency}</span>
                  </span>
                </td>
                <td>
                  <span className="svc-chips">
                    {GESTURE_KINDS.map(k => {
                      const on = r.allowed_kinds.includes(k);
                      return (
                        <label key={k} className={`svc-chip${on ? " svc-chip--gold" : ""}`}>
                          <input type="checkbox" checked={on} disabled={ro} style={{ width: "auto", minWidth: 0 }}
                                 onChange={e => state.setRow(i, {
                                   allowed_kinds: e.target.checked ? [...r.allowed_kinds, k] : r.allowed_kinds.filter(x => x !== k),
                                 })} />
                          {k.replace(/_/g, " ")}
                        </label>
                      );
                    })}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </SectionFrame>
  );
}
