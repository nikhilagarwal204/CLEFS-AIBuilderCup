"use client";

/* Editable tables for the Service setup page. Each section edits a local
   copy and saves the whole section at once (a hotel's overrides are a
   handful of rows; a per-row save would scatter audit entries). */

import type { ReactNode } from "react";
import { Button, Card, EmptyState, Skeleton } from "../../../../../ui";
import { SEED_DEPARTMENTS, SEED_REQUEST_TYPES, type Department, type RequestType } from "../_components/catalog";
import type { SectionState } from "./useSection";

// ─── Frame ───

export function SectionFrame<T>({
  title, subtitle, state, onAdd, children,
}: {
  title: string;
  subtitle?: ReactNode;
  state: SectionState<T>;
  onAdd?: () => void;
  children: ReactNode;
}) {
  const readOnly = state.offline;
  return (
    <Card title={title} subtitle={subtitle} actions={
      readOnly ? undefined : (
        <div className="svc-row">
          {onAdd && <Button size="sm" onClick={onAdd}>Add</Button>}
          <Button size="sm" variant="ghost" onClick={state.reset} disabled={!state.dirty || state.saving}>Undo</Button>
          <Button size="sm" variant="primary" onClick={() => void state.save()} disabled={!state.dirty} loading={state.saving}>Save</Button>
        </div>
      )
    }>
      {state.offline && (
        <p className="svc-muted" style={{ marginBottom: 12 }}>
          Showing the Clefs defaults. Editing opens once this hotel&apos;s setup service is live.
        </p>
      )}
      {state.error && <EmptyState title="Could not load this section" body={state.error} />}
      {state.loading ? <Skeleton lines={5} /> : !state.error && <div className="svc-scroll">{children}</div>}
    </Card>
  );
}

// ─── Departments ───

export function DepartmentsSection({ state }: { state: SectionState<Department> }) {
  const ro = state.offline;
  return (
    <SectionFrame title="Departments" subtitle="Names staff and guests see; the legacy category keeps old screens working."
                  state={state}
                  onAdd={() => state.addRow({ key: "", name: "", legacy_category: "General", active: true, sort: 100 })}>
      <table className="svc-table">
        <thead><tr><th>Key</th><th>Name</th><th>Arabic</th><th>Legacy category</th><th>Order</th><th>Active</th></tr></thead>
        <tbody>
          {state.rows.map((d, i) => {
            const seeded = SEED_DEPARTMENTS.some(s => s.key === d.key);
            return (
              <tr key={`${d.key}-${i}`}>
                <td><input aria-label="Key" value={d.key} disabled={ro || seeded}
                           onChange={e => state.setRow(i, { key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} /></td>
                <td><input aria-label="Name" value={d.name} disabled={ro} onChange={e => state.setRow(i, { name: e.target.value })} /></td>
                <td><input aria-label="Arabic name" dir="rtl" lang="ar" value={d.name_ar ?? ""} disabled={ro}
                           onChange={e => state.setRow(i, { name_ar: e.target.value })} /></td>
                <td><input aria-label="Legacy category" value={d.legacy_category} disabled={ro}
                           onChange={e => state.setRow(i, { legacy_category: e.target.value })} /></td>
                <td><input aria-label="Order" type="number" value={d.sort ?? 0} disabled={ro} style={{ width: 70 }}
                           onChange={e => state.setRow(i, { sort: Number(e.target.value) })} /></td>
                <td><input aria-label="Active" type="checkbox" checked={d.active !== false} disabled={ro}
                           onChange={e => state.setRow(i, { active: e.target.checked })} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </SectionFrame>
  );
}

// ─── Request types ───

export function RequestTypesSection({ state, departments }: { state: SectionState<RequestType>; departments: Department[] }) {
  const ro = state.offline;
  return (
    <SectionFrame title="Request types" subtitle="What guests and the desk can ask for, which team gets it, and its default level."
                  state={state}
                  onAdd={() => state.addRow({ key: "", label_en: "", department_key: "general", default_severity: "L3", is_amenity: false, active: true })}>
      <table className="svc-table">
        <thead>
          <tr><th>Key</th><th>Label</th><th>Arabic</th><th>Department</th><th>Level</th><th>Amenity</th><th>Sensitive</th><th>Active</th></tr>
        </thead>
        <tbody>
          {state.rows.map((t, i) => {
            const seeded = SEED_REQUEST_TYPES.some(s => s.key === t.key);
            return (
              <tr key={`${t.key}-${i}`}>
                <td><input aria-label="Key" value={t.key} disabled={ro || seeded}
                           onChange={e => state.setRow(i, { key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} /></td>
                <td><input aria-label="Label" value={t.label_en} disabled={ro} onChange={e => state.setRow(i, { label_en: e.target.value })} /></td>
                <td><input aria-label="Arabic label" dir="rtl" lang="ar" value={t.label_ar ?? ""} disabled={ro}
                           onChange={e => state.setRow(i, { label_ar: e.target.value })} /></td>
                <td>
                  <select aria-label="Department" value={t.department_key} disabled={ro}
                          onChange={e => state.setRow(i, { department_key: e.target.value })}>
                    {departments.map(d => <option key={d.key} value={d.key}>{d.name}</option>)}
                  </select>
                </td>
                <td>
                  <select aria-label="Level" value={t.default_severity} disabled={ro}
                          onChange={e => state.setRow(i, { default_severity: e.target.value as RequestType["default_severity"] })}>
                    <option value="L1">L1</option><option value="L2">L2</option><option value="L3">L3</option>
                  </select>
                </td>
                <td><input aria-label="Amenity" type="checkbox" checked={t.is_amenity} disabled={ro}
                           onChange={e => state.setRow(i, { is_amenity: e.target.checked })} /></td>
                <td><input aria-label="Sensitive" type="checkbox" checked={!!t.sensitive} disabled={ro}
                           onChange={e => state.setRow(i, { sensitive: e.target.checked })} /></td>
                <td><input aria-label="Active" type="checkbox" checked={t.active !== false} disabled={ro}
                           onChange={e => state.setRow(i, { active: e.target.checked })} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </SectionFrame>
  );
}
