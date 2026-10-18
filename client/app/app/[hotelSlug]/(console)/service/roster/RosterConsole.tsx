"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Weekly roster (03 spec 1.6): who is on, when, in which role and on which
   floors. The roster feeds routing (floor zones), the SLA ladder
   (supervisor / duty manager on shift now) and the device lock screen.
   Labour guardrails run on validate and save: warnings, plus the summer
   midday outdoor ban, which needs a written reason (audited).
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useMemo, useState } from "react";
import { hotelPath, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, Field, Modal, Skeleton, Textarea, useToast } from "../../../../../ui";
import ServiceShell from "../_components/ServiceShell";
import { useServiceCatalog } from "../_components/catalog";
import { addDays, hotelWeekStart } from "../_components/hotelTime";
import { useNow } from "../_components/RequestMeta";
import { errorText, isMissingRoute } from "../_components/serviceApi";
import {
  CAPTION, flagLevel, flagText, loadRoster, saveRoster, validateRoster,
  type Assignment, type LabourFlag, type ShiftRole, type ShiftTemplate,
} from "../_components/rosterApi";
import ShiftModal, { type ShiftTarget } from "./ShiftModal";

interface Staff { id: number; name: string; role: string; department: string; is_active: boolean }

// Service seeds (constants.SHIFT_TEMPLATES) for a hotel whose roster route
// has no templates yet. Negative ids never reach the server.
const SEED_TEMPLATES: ShiftTemplate[] = [
  { id: -1, key: "morning", name: "Morning", start_time: "07:00", end_time: "15:00" },
  { id: -2, key: "evening", name: "Evening", start_time: "15:00", end_time: "23:00" },
  { id: -3, key: "night", name: "Night", start_time: "23:00", end_time: "07:00", crosses_midnight: true },
  { id: -4, key: "hk_day", name: "Housekeeping day", department_key: "housekeeping", start_time: "08:00", end_time: "17:00" },
  { id: -5, key: "hk_eve", name: "Housekeeping evening", department_key: "housekeeping", start_time: "15:00", end_time: "24:00" },
];

export default function RosterConsole() {
  const { slug } = useHotel();
  const hf = useFormat();
  const { can, role } = useSession();
  const toast = useToast();
  const now = useNow(600_000);
  const canEdit = can("service.manage");
  // Mirrors the server's cap (MEMBERSHIP_AUTHORITY): owner/GM any role, a
  // department head up to supervisor, everyone else attendants only.
  const maxRole: ShiftRole = can("core.settings.manage") ? "duty_manager" : role === "dept_head" ? "supervisor" : "attendant";
  const catalog = useServiceCatalog(slug);
  const staffRes = useApi<Staff[]>(hotelPath(slug, "/staff"), [slug]);

  // The first week shown is the hotel's current week (not the browser's);
  // the arrows pick another one.
  const [weekPick, setWeekPick] = useState<string | null>(null);
  const week = weekPick ?? (now ? hotelWeekStart(now, hf.tz) : "");
  const [templates, setTemplates] = useState<ShiftTemplate[]>(SEED_TEMPLATES);
  const [rows, setRows] = useState<Assignment[]>([]);
  const [flags, setFlags] = useState<LabourFlag[]>([]);
  const [loadedKey, setLoadedKey] = useState("");
  const [offline, setOffline] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"" | "validate" | "save">("");
  const [target, setTarget] = useState<ShiftTarget | null>(null);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [reason, setReason] = useState("");
  const loading = !week || loadedKey !== week;

  useEffect(() => {
    if (!week) return;
    let live = true;
    loadRoster(slug, week)
      .then(r => {
        if (!live) return;
        setTemplates(r.templates.length ? r.templates : SEED_TEMPLATES);
        setRows(r.assignments.filter(a => a.status !== "cancelled"));
        setFlags(r.flags ?? []);
        setOffline(false); setDirty(false);
      })
      .catch(err => {
        if (!live) return;
        if (isMissingRoute(err)) { setOffline(true); setRows([]); setFlags([]); }
        else toast.error("Could not load the roster", errorText(err));
      })
      .finally(() => { if (live) setLoadedKey(week); });
    return () => { live = false; };
  }, [slug, week, toast]);

  const days = useMemo(() => (week ? Array.from({ length: 7 }, (_, i) => addDays(week, i)) : []), [week]);
  const staff = (staffRes.data ?? []).filter(s => s.is_active);
  const blocked = flags.filter(f => flagLevel(f) === "blocked");

  // ─── Edits ───
  const outgoing = (withReason?: string): Assignment[] =>
    rows.map(a => ({
      ...a,
      template_id: a.template_id && a.template_id > 0 ? a.template_id : null,
      override_reason: withReason && flags.some(f => flagLevel(f) === "blocked" && f.staff_member_id === a.staff_member_id
        && f.business_date === a.business_date) ? withReason : a.override_reason ?? null,
    }));

  const addShift = (a: Assignment) => { setRows(prev => [...prev, a]); setDirty(true); };
  const removeShift = (a: Assignment) => { setRows(prev => prev.filter(x => x !== a)); setDirty(true); };

  const validate = async () => {
    setBusy("validate");
    try {
      const out = await validateRoster(slug, week, outgoing());
      setFlags(out.flags ?? []);
      if (!(out.flags ?? []).length) toast.success("No labour flags this week.");
    } catch (err) {
      toast.error("Could not check the roster", errorText(err));
    } finally { setBusy(""); }
  };

  const save = async (withReason?: string) => {
    setBusy("save");
    try {
      const out = await saveRoster(slug, week, outgoing(withReason));
      if (Array.isArray(out.assignments)) setRows(out.assignments.filter(a => a.status !== "cancelled"));
      setFlags(Array.isArray(out.flags) ? out.flags : []);
      setDirty(false); setOverrideOpen(false); setReason("");
      toast.success("Roster saved.");
    } catch (err) {
      // A blocked rule (midday ban) answers 409/422 until a reason is given.
      // The server answers 409 {detail, code: labour_blocked, flags, caption}:
      // the flags sit next to detail, so read the raw error body first.
      const e = err as { body?: { flags?: LabourFlag[] }; detail?: { flags?: LabourFlag[] } };
      const body = Array.isArray(e.body?.flags) ? e.body : e.detail;
      if (body && Array.isArray(body.flags)) setFlags(body.flags);
      if (blocked.length || (body?.flags ?? []).some(f => flagLevel(f) === "blocked")) setOverrideOpen(true);
      else toast.error("Could not save the roster", errorText(err));
    } finally { setBusy(""); }
  };

  const flagsFor = (staffId: number, day: string) =>
    flags.filter(f => f.staff_member_id === staffId && (!f.business_date || f.business_date === day));
  const time = (iso: string) => hf.time(iso);
  const shiftLabel = (a: Assignment) => templates.find(t => t.id === a.template_id)?.name ?? "Shift";

  // ─── Render ───
  return (
    <ServiceShell tab="roster" perm={["service.view", "service.manage"]} title="Roster"
                  subtitle="Who is on, in which role, on which floors. Supervisors and duty managers on shift receive the escalations."
                  actions={canEdit && !offline ? (
                    <div className="svc-row">
                      <Button onClick={() => void validate()} loading={busy === "validate"} disabled={!week}>Check labour rules</Button>
                      <Button variant="primary" onClick={() => void save()} loading={busy === "save"} disabled={!dirty}>Save week</Button>
                    </div>
                  ) : undefined}>
      <div className="svc-stack">
        <div className="svc-row" style={{ justifyContent: "space-between" }}>
          <div className="svc-row">
            <Button size="sm" onClick={() => setWeekPick(addDays(week, -7))} disabled={!week || dirty}>Previous week</Button>
            <strong>{week ? `${hf.date(days[0])} to ${hf.date(days[6])}` : ""}</strong>
            <Button size="sm" onClick={() => setWeekPick(addDays(week, 7))} disabled={!week || dirty}>Next week</Button>
          </div>
          <span className="svc-caption">{CAPTION}</span>
        </div>
        {offline && (
          <Card><p className="svc-muted">The roster service is not live on this server yet. Shifts can be planned once it is.</p></Card>
        )}
        {blocked.length > 0 && (
          <Card><p className="svc-flag svc-flag--bad">{blocked.length} shift{blocked.length === 1 ? "" : "s"} break the summer midday outdoor ban. Saving needs a reason.</p></Card>
        )}
        {loading || staffRes.loading ? <Skeleton lines={6} /> : staff.length === 0 ? (
          <EmptyState title="No staff yet" body="Add your team in the live queue's Staff view first." />
        ) : (
          <Card padded={false}>
            <div className="svc-scroll">
              <table className="svc-table svc-roster">
                <thead>
                  <tr><th>Staff</th>{days.map(d => <th key={d}>{hf.date(d, "medium").split(",")[0]} {d.slice(8)}</th>)}</tr>
                </thead>
                <tbody>
                  {staff.map(s => (
                    <tr key={s.id}>
                      <td><strong>{s.name}</strong><div className="svc-faint">{s.department}</div></td>
                      {days.map(d => {
                        const mine = rows.filter(a => a.staff_member_id === s.id && a.business_date === d);
                        const fl = flagsFor(s.id, d);
                        return (
                          <td key={d}>
                            {mine.map((a, i) => (
                              <div key={i} className={`svc-shift${fl.some(f => flagLevel(f) === "blocked") ? " svc-shift--bad" : fl.length ? " svc-shift--warn" : ""}`}>
                                <span>{shiftLabel(a)} {time(a.start_at)}-{time(a.end_at)}</span>
                                {a.shift_role !== "attendant" && <span className="svc-faint">{a.shift_role === "supervisor" ? "Supervisor" : "Duty manager"}</span>}
                                {a.floors.length > 0 && <span className="svc-faint">Floors {a.floors.join(", ")}</span>}
                                {canEdit && !offline && <button type="button" className="svc-linkbtn" onClick={() => removeShift(a)}>Remove</button>}
                              </div>
                            ))}
                            {fl.map((f, i) => <div key={`f${i}`} className={`svc-flag${flagLevel(f) === "blocked" ? " svc-flag--bad" : ""}`}>{flagText(f)}</div>)}
                            {canEdit && !offline && (
                              <button type="button" className="svc-linkbtn" onClick={() => setTarget({ staffId: s.id, staffName: s.name, day: d })}>+ Shift</button>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      {target && (
        <ShiftModal key={`${target.staffId}-${target.day}`} target={target} templates={templates} departments={catalog.departments}
                    tz={hf.tz} onClose={() => setTarget(null)} onAdd={addShift} maxRole={maxRole} />
      )}
      <Modal open={overrideOpen} onClose={() => setOverrideOpen(false)} title="Override the midday outdoor ban" size="sm"
             dismissible={busy !== "save"}
             footer={<>
               <Button variant="ghost" onClick={() => setOverrideOpen(false)}>Back</Button>
               <Button variant="danger" onClick={() => void save(reason.trim())} disabled={reason.trim().length < 10} loading={busy === "save"}>Save with reason</Button>
             </>}>
        <Field label="Why is this outdoor work needed between 12:30 and 15:00?" hint="Recorded in the audit log with your name." htmlFor="svc-override">
          <Textarea id="svc-override" rows={3} value={reason} onChange={e => setReason(e.target.value)} />
        </Field>
      </Modal>
    </ServiceShell>
  );
}
