"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Brief settings (brief.manage to change, brief.view to read): the send
   time in the hotel's timezone, which tiers go out, who receives them,
   the channel order, how many items and whether an empty brief is sent.
   PUT sends only what changed; the time is the core hotels column the
   scheduler reads, the rest is hotel_module_settings["brief"].
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { LuArrowUp } from "react-icons/lu";
import { api, useApi, type ApiError } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Skeleton, useToast } from "../../../../../ui";
import { brfApi, CADENCE_LABEL, CADENCES, PERM, roleLabel } from "../lib";
import type { BriefChannel, BriefSettingsView, SettingsPatch, WaContact } from "../types";
import QuietHours from "./QuietHours";

const CHANNEL_LABEL: Record<BriefChannel, string> = { whatsapp: "WhatsApp", email: "Email" };
const CADENCE_HINT = {
  daily: "Money at risk today: deadlines, unanswered inquiries, parity gaps, urgent service.",
  weekly: "Monday: visibility, rank, reviews, the NRevPAR trend, autonomy suggestions and compliance.",
  monthly: "The 1st: the P&L pulse for last month.",
} as const;

export default function SettingsForm() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const canManage = can(PERM.manage);
  const s = useApi<BriefSettingsView>(brfApi(slug, "/settings"), [slug]);
  const contacts = useApi<WaContact[]>(brfApi(slug, "/wa/contacts"), [slug]);
  const [draft, setDraft] = useState<SettingsPatch>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const v = useMemo(() => (s.data ? { ...s.data, ...draft } : null), [s.data, draft]);
  const dirty = Object.keys(draft).length > 0;
  const set = (patch: SettingsPatch) => setDraft(d => ({ ...d, ...patch }));

  if (s.loading && !s.data) return <Card><Skeleton lines={6} /></Card>;
  if (s.error && !s.data) {
    return <EmptyState title="Could not load the settings" body={s.error.message} action={<Button onClick={s.reload}>Try again</Button>} />;
  }
  if (!v || !s.data) return null;

  const picked = new Set(v.recipient_user_ids);
  const toggleRecipient = (id: number) => {
    // The first pick starts from the default audience, so ticking one more
    // person does not silently drop the owners and GMs already receiving it.
    const next = new Set(picked.size ? picked : v.recipients.map(r => r.user_id));
    if (next.has(id)) next.delete(id); else next.add(id);
    set({ recipient_user_ids: [...next] });
  };
  const toggleChannel = (c: BriefChannel) => {
    const has = v.channels.includes(c);
    const next = has ? v.channels.filter(x => x !== c) : [...v.channels, c];
    if (!next.length) return setErr("Keep at least one channel.");
    setErr(null);
    set({ channels: next });
  };
  const raise = (c: BriefChannel) => set({ channels: [c, ...v.channels.filter(x => x !== c)] });

  const save = async () => {
    if (v.brief_time_local && !/^([01]\d|2[0-3]):[0-5]\d$/.test(v.brief_time_local)) return setErr("Enter a time like 07:00.");
    setSaving(true);
    setErr(null);
    try {
      const out = await api.put<BriefSettingsView>(brfApi(slug, "/settings"), draft);
      s.setData(out);
      setDraft({});
      toast.success("Brief settings saved");
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae.code === "invalid_recipient" ? "One of the recipients is no longer a member here."
        : ae.code === "invalid_quiet_hours" ? "Set one quiet window per person." : ae.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Delivery" subtitle={`Times are in the hotel's timezone (${s.data.timezone}).`}
          actions={canManage ? (
            <>
              {dirty && <Button size="sm" variant="ghost" disabled={saving} onClick={() => { setDraft({}); setErr(null); }}>Discard</Button>}
              <Button size="sm" variant="primary" disabled={!dirty} loading={saving} onClick={() => void save()}>Save</Button>
            </>
          ) : <Badge tone="neutral">Read only</Badge>}>
      <fieldset disabled={!canManage || saving} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }} className="cx-stack">
        <div className="brf-grid2">
          <Field label="Daily brief time" hint="Weekly goes Monday 08:00 and monthly the 1st at 08:00.">
            <Input type="time" step={60} value={v.brief_time_local} onChange={e => set({ brief_time_local: e.target.value.slice(0, 5) })} />
          </Field>
          <Field label="Items per brief" hint="Ranked items (1 to 10). Overdue pins come on top of these.">
            <Input type="number" min={1} max={10} value={v.top_n}
                   onChange={e => set({ top_n: Math.max(1, Math.min(10, Number(e.target.value) || 1)) })} />
          </Field>
        </div>

        <div className="brf-block">
          <span className="cx-label">Which briefs</span>
          <div className="brf-checks">
            {CADENCES.map(c => {
              const key = `${c}_enabled` as const;
              return (
                <label key={c} className="brf-check">
                  <input type="checkbox" checked={v[key]} onChange={e => set({ [key]: e.target.checked })} />
                  <span><b>{CADENCE_LABEL[c]}</b> <span className="brf-muted">{CADENCE_HINT[c]}</span></span>
                </label>
              );
            })}
            <label className="brf-check">
              <input type="checkbox" checked={v.send_when_empty} onChange={e => set({ send_when_empty: e.target.checked })} />
              <span>Send even when nothing needs a decision <span className="brf-muted">(the verified line still goes)</span></span>
            </label>
          </div>
        </div>

        <div className="brf-block">
          <span className="cx-label">Channels, in the order tried</span>
          <div className="brf-checks">
            {(["whatsapp", "email"] as const).slice().sort((a, b) => {
              const ia = v.channels.indexOf(a), ib = v.channels.indexOf(b);
              return (ia < 0 ? 9 : ia) - (ib < 0 ? 9 : ib);
            }).map(c => (
              <div key={c} className="brf-order">
                <label className="brf-check">
                  <input type="checkbox" checked={v.channels.includes(c)} onChange={() => toggleChannel(c)} />
                  {v.channels.includes(c) ? `${v.channels.indexOf(c) + 1}. ` : ""}{CHANNEL_LABEL[c]}
                </label>
                {v.channels.indexOf(c) > 0 && (
                  <Button size="sm" variant="ghost" icon={<LuArrowUp />} aria-label={`Try ${CHANNEL_LABEL[c]} first`} onClick={() => raise(c)} />
                )}
              </div>
            ))}
          </div>
          <p className="brf-muted" style={{ margin: 0 }}>
            WhatsApp goes only once Meta approves the brief template; until then, and whenever a WhatsApp brief is not delivered within 30 minutes, email goes instead.
          </p>
        </div>

        <Recipients v={v} contacts={contacts.data} picked={picked} onToggle={toggleRecipient} onReset={() => set({ recipient_user_ids: [] })} />
        <QuietHours value={v.quiet_hours ?? []} onChange={next => set({ quiet_hours: next })}
                    people={(contacts.data ?? v.recipients).map(p => ({ user_id: p.user_id, name: p.name, role: String(p.role) }))} />
        {err && <p className="cx-error-text" role="alert" style={{ margin: 0 }}>{err}</p>}
      </fieldset>
    </Card>
  );
}

// ─── Recipients ──────────────────────────────────────────────────────────

/* Empty recipient_user_ids means "every active owner and GM" (the server
   default), so a new hotel's brief reaches someone without any setup. The
   list offers every member who may read the brief (owners, GMs, finance,
   revenue, department heads); the resolved list shows who it goes to now. */
function Recipients({ v, contacts, picked, onToggle, onReset }: {
  v: BriefSettingsView & SettingsPatch;
  contacts: WaContact[] | undefined;
  picked: ReadonlySet<number>;
  onToggle: (id: number) => void;
  onReset: () => void;
}) {
  const current = new Map(v.recipients.map(r => [r.user_id, r] as const));
  const people = contacts ?? v.recipients.map(r => ({ user_id: r.user_id, name: r.name, role: r.role, has_phone: r.has_whatsapp } as WaContact));
  const byDefault = picked.size === 0;
  return (
    <div className="brf-block">
      <div className="brf-row">
        <span className="cx-label brf-spacer">Who receives it</span>
        {!byDefault && <Button size="sm" variant="ghost" onClick={onReset}>Use owners and GMs</Button>}
      </div>
      {byDefault && <p className="brf-muted" style={{ margin: 0 }}>Every active owner and GM, until you pick people below.</p>}
      <div className="brf-checks">
        {people.map(p => {
          const r = current.get(p.user_id);
          return (
            <label key={p.user_id} className="brf-check">
              <input type="checkbox" checked={byDefault ? !!r : picked.has(p.user_id)} onChange={() => onToggle(p.user_id)} />
              <span className="brf-spacer">{p.name} <span className="brf-muted">{roleLabel(String(p.role))}</span></span>
              {r && !r.has_email && !r.has_whatsapp && <Badge tone="warn">No email or phone</Badge>}
              {r && r.has_whatsapp && <Badge tone="neutral">WhatsApp</Badge>}
              {r && r.has_email && <Badge tone="neutral">Email</Badge>}
            </label>
          );
        })}
      </div>
    </div>
  );
}
