"use client";

/* Add one shift to a staff member's day: a template (or custom times), the
   shift role (supervisor and duty manager rows drive the SLA ladder), the
   department and the floors this person covers. */

import { useState } from "react";
import { Button, Field, Input, Modal, Select } from "../../../../../ui";
import type { Department } from "../_components/catalog";
import { hotelInstant } from "../_components/hotelTime";
import type { Assignment, ShiftRole, ShiftTemplate } from "../_components/rosterApi";

const hhmm = (t: string) => t.slice(0, 5);

export interface ShiftTarget { staffId: number; staffName: string; day: string; department?: string | null }

const ROLE_RANK: Record<ShiftRole, number> = { attendant: 0, supervisor: 1, duty_manager: 2 };

export default function ShiftModal({
  target, templates, departments, tz, onClose, onAdd, maxRole = "duty_manager",
}: {
  target: ShiftTarget | null;
  templates: ShiftTemplate[];
  departments: Department[];
  tz: string;
  onClose: () => void;
  onAdd: (a: Assignment) => void;
  /** The highest shift role this user may grant. A supervisor or duty-manager
   *  shift lifts a device user's compensation authority, so the server caps it
   *  at the caller's own (roster.check_shift_role_authority); this mirrors it. */
  maxRole?: ShiftRole;
}) {
  const active = templates.filter(t => t.active !== false);
  const [templateId, setTemplateId] = useState<string>(active[0] ? String(active[0].id) : "custom");
  const [start, setStart] = useState("07:00");
  const [end, setEnd] = useState("15:00");
  const [role, setRole] = useState<ShiftRole>("attendant");
  const [dept, setDept] = useState<string>("");
  const [floors, setFloors] = useState("");
  if (!target) return null;

  const tpl = active.find(t => String(t.id) === templateId) ?? null;
  const s = tpl ? hhmm(tpl.start_time) : start;
  const e = tpl ? hhmm(tpl.end_time) : end;
  const overnight = tpl ? (tpl.crosses_midnight ?? e <= s) : e <= s;

  const add = () => {
    const startAt = hotelInstant(target.day, s, tz);
    // An overnight shift ends on the next calendar day.
    let endAt = hotelInstant(target.day, e, tz);
    if (overnight || endAt <= startAt) endAt += 24 * 3_600_000;
    onAdd({
      staff_member_id: target.staffId,
      template_id: tpl?.id ?? null,
      business_date: target.day,
      start_at: new Date(startAt).toISOString(),
      end_at: new Date(endAt).toISOString(),
      department_key: dept || tpl?.department_key || null,
      floors: floors.split(/[\s,]+/).map(f => f.trim()).filter(Boolean),
      shift_role: role,
      status: "planned",
    });
    onClose();
  };

  return (
    <Modal open onClose={onClose} title={`${target.staffName}, ${target.day}`} size="sm"
           footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={add}>Add shift</Button></>}>
      <div className="svc-stack">
        <Field label="Shift" htmlFor="svc-shift-tpl">
          <Select id="svc-shift-tpl" value={templateId} onChange={ev => setTemplateId(ev.target.value)}>
            {active.map(t => <option key={t.id} value={String(t.id)}>{t.name} ({hhmm(t.start_time)}-{hhmm(t.end_time)})</option>)}
            <option value="custom">Custom times</option>
          </Select>
        </Field>
        {!tpl && (
          <div className="svc-row">
            <Field label="Starts" htmlFor="svc-shift-s"><Input id="svc-shift-s" type="time" value={start} onChange={ev => setStart(ev.target.value)} /></Field>
            <Field label="Ends" htmlFor="svc-shift-e"><Input id="svc-shift-e" type="time" value={end} onChange={ev => setEnd(ev.target.value)} /></Field>
          </div>
        )}
        {overnight && <p className="svc-faint">Ends the next morning.</p>}
        <Field label="Role on this shift" htmlFor="svc-shift-role" hint={maxRole === "duty_manager"
          ? "Supervisors and duty managers receive the escalations for this shift."
          : "Supervisors and duty managers receive the escalations. Higher shift roles are assigned by an owner or GM."}>
          <Select id="svc-shift-role" value={role} onChange={ev => setRole(ev.target.value as ShiftRole)}>
            <option value="attendant">Attendant</option>
            <option value="supervisor" disabled={ROLE_RANK.supervisor > ROLE_RANK[maxRole]}>Supervisor</option>
            <option value="duty_manager" disabled={ROLE_RANK.duty_manager > ROLE_RANK[maxRole]}>Duty manager</option>
          </Select>
        </Field>
        <Field label="Department" htmlFor="svc-shift-dept">
          <Select id="svc-shift-dept" value={dept} onChange={ev => setDept(ev.target.value)}>
            <option value="">{tpl?.department_key ? "From the shift" : "Their usual department"}</option>
            {departments.map(d => <option key={d.key} value={d.key}>{d.name}</option>)}
          </Select>
        </Field>
        <Field label="Floors" htmlFor="svc-shift-floors" hint="Comma separated, e.g. 3, 4. Empty means every floor.">
          <Input id="svc-shift-floors" value={floors} onChange={ev => setFloors(ev.target.value)} placeholder="3, 4" />
        </Field>
      </div>
    </Modal>
  );
}
