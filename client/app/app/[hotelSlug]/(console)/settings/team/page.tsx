"use client";

/* Team & roles: hotel memberships. A role is a bundle of permissions the
   server resolves (the client never hardcodes the matrix); departments and
   floors scope a dept_head or staff member to their part of the hotel. */

import { useState, type FormEvent } from "react";
import { LuPencil, LuPlus, LuTrash2 } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { ROLE_LABELS, ROLES, type Role, type TeamMember, type TeamMemberCreate, type TeamMemberPatch } from "../../../../../lib/types";
import { Badge, Button, Card, Field, Input, Modal, Select, Table, useToast, type Column } from "../../../../../ui";
import { asList, DEPARTMENT_SUGGESTIONS, joinList, splitList } from "../shared";

const ROLE_HINTS: Record<Role, string> = {
  owner: "Everything, including money, team and settings.",
  gm: "Runs the hotel: everything an owner sees.",
  finance: "Profit Guard, finance reports and recoveries.",
  revenue: "Demand, pricing guardrails and channel mix.",
  dept_head: "Their departments' requests, staff and SLAs.",
  front_desk: "Service requests and guest profiles.",
  staff: "Their own tasks.",
};

/** The server may nest the user; flatten to TeamMember. */
function normalize(raw: Record<string, unknown>): TeamMember {
  const user = (raw.user ?? {}) as Record<string, unknown>;
  return {
    id: Number(raw.id ?? raw.membership_id),
    user_id: Number(raw.user_id ?? user.id),
    name: String(raw.name ?? user.name ?? ""),
    email: (raw.email ?? user.email ?? null) as string | null,
    phone: (raw.phone ?? raw.phone_e164 ?? user.phone ?? user.phone_e164 ?? null) as string | null,
    role: raw.role as Role,
    departments: (raw.departments as string[]) ?? [],
    floors: (raw.floors as string[]) ?? [],
    is_active: raw.is_active !== false,
    last_login_at: (raw.last_login_at ?? user.last_login_at ?? null) as string | null,
  };
}

interface Draft {
  name: string;
  email: string;
  phone: string;
  role: Role;
  departments: string;
  floors: string;
  is_active: boolean;
}

const EMPTY: Draft = { name: "", email: "", phone: "", role: "staff", departments: "", floors: "", is_active: true };

export default function TeamPage() {
  const { slug } = useHotel();
  const session = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = session.can("core.team.manage");

  const { data, loading, error, reload } = useApi<unknown>(hotelPath(slug, "/team"), [slug]);
  const members = asList<Record<string, unknown>>(data, "members").map(normalize);

  const [editing, setEditing] = useState<TeamMember | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState<TeamMember | null>(null);

  const open = (m: TeamMember | "new") => {
    setErrors({});
    setDraft(m === "new" ? EMPTY : {
      name: m.name, email: m.email ?? "", phone: m.phone ?? "", role: m.role,
      departments: joinList(m.departments), floors: joinList(m.floors), is_active: m.is_active,
    });
    setEditing(m);
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (editing === "new") {
      if (!draft.name.trim()) errs.name = "Name is required.";
      if (!/^\S+@\S+\.\S+$/.test(draft.email.trim())) errs.email = "A valid email is required; the invite goes here.";
    }
    if (draft.phone.trim() && !/^\+?[\d\s-]{7,20}$/.test(draft.phone.trim())) errs.phone = "International format, e.g. +971 50 123 4567.";
    if ((draft.role === "dept_head") && !splitList(draft.departments).length) errs.departments = "A department head needs at least one department.";
    setErrors(errs);
    if (Object.keys(errs).length) return;

    setSaving(true);
    try {
      if (editing === "new") {
        const body: TeamMemberCreate = {
          name: draft.name.trim(), email: draft.email.trim().toLowerCase(), phone: draft.phone.trim() || null,
          role: draft.role, departments: splitList(draft.departments), floors: splitList(draft.floors),
        };
        await api.post(hotelPath(slug, "/team"), body);
        toast.success("Member added", `${body.name} can now sign in to this hotel.`);
      } else if (editing) {
        const body: TeamMemberPatch = {
          role: draft.role, departments: splitList(draft.departments), floors: splitList(draft.floors),
          is_active: draft.is_active, phone: draft.phone.trim() || null,
        };
        await api.patch(hotelPath(slug, `/team/${editing.id}`), body);
        toast.success("Member updated");
      }
      setEditing(null);
      reload();
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      if (apiErr?.status === 422) setErrors(apiErr.fieldErrors);
      toast.error("Couldn't save", apiErr?.message ?? String(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!removing) return;
    setSaving(true);
    try {
      await api.del(hotelPath(slug, `/team/${removing.id}`));
      toast.success("Member removed", `${removing.name} no longer has access to this hotel.`);
      setRemoving(null);
      reload();
    } catch (err) {
      toast.error("Couldn't remove", err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const columns: Column<TeamMember>[] = [
    {
      key: "name", header: "Member",
      render: m => (
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{m.name}{m.user_id === session.user?.id ? <span className="cx-faint"> (you)</span> : null}</div>
          <div className="cx-faint" style={{ fontSize: "0.78rem", overflowWrap: "anywhere" }}>{m.email ?? m.phone ?? ""}</div>
        </div>
      ),
    },
    { key: "role", header: "Role", render: m => <Badge tone={m.role === "owner" || m.role === "gm" ? "gold" : "neutral"}>{ROLE_LABELS[m.role] ?? m.role}</Badge> },
    { key: "scope", header: "Scope", render: m => {
      const parts = [joinList(m.departments), m.floors.length ? `Floors ${joinList(m.floors)}` : ""].filter(Boolean);
      return <span className="cx-muted">{parts.join(" · ") || "Whole hotel"}</span>;
    } },
    { key: "status", header: "Status", render: m => <Badge tone={m.is_active ? "ok" : "neutral"} dot>{m.is_active ? "Active" : "Suspended"}</Badge> },
    { key: "last", header: "Last sign-in", hideOnMobile: true, render: m => <span className="cx-muted">{m.last_login_at ? fmt.relative(m.last_login_at) : "Never"}</span> },
    ...(canManage ? [{
      key: "actions", header: "", align: "right" as const, mobileLabel: "",
      render: (m: TeamMember) => (
        <div className="cx-row" style={{ gap: 4, justifyContent: "flex-end" }}>
          <Button size="sm" variant="ghost" icon={<LuPencil />} aria-label={`Edit ${m.name}`} onClick={() => open(m)} />
          <Button size="sm" variant="ghost" icon={<LuTrash2 />} aria-label={`Remove ${m.name}`}
                  disabled={m.user_id === session.user?.id} title={m.user_id === session.user?.id ? "You can't remove yourself" : undefined}
                  onClick={() => setRemoving(m)} />
        </div>
      ),
    }] : []),
  ];

  return (
    <div className="cx-stack">
      <Card
        title="Team"
        subtitle={canManage ? "Invite people by email. Roles decide which modules they see." : "Only owners and GMs can change the team."}
        actions={canManage ? <Button variant="primary" size="sm" icon={<LuPlus />} onClick={() => open("new")}>Add member</Button> : undefined}
      >
        {error ? (
          <div className="cx-row">
            <span className="cx-error-text">{error.message}</span>
            <Button size="sm" onClick={reload}>Retry</Button>
          </div>
        ) : (
          <Table columns={columns} rows={members} rowKey={m => m.id} loading={loading} caption="Hotel team members"
                 empty="No one else is on the team yet." />
        )}
      </Card>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        dismissible={!saving}
        title={editing === "new" ? "Add a team member" : `Edit ${editing?.name ?? "member"}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)} disabled={saving}>Cancel</Button>
            <Button variant="primary" type="submit" form="cx-team-form" loading={saving}>
              {editing === "new" ? "Add member" : "Save"}
            </Button>
          </>
        }
      >
        <form id="cx-team-form" onSubmit={save} noValidate className="cx-form-grid">
          {editing === "new" && (
            <>
              <Field label="Full name" required error={errors.name}>
                <Input value={draft.name} invalid={!!errors.name} autoComplete="off" onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
              </Field>
              <Field label="Email" required error={errors.email}>
                <Input type="email" value={draft.email} invalid={!!errors.email} autoComplete="off" onChange={e => setDraft(d => ({ ...d, email: e.target.value }))} />
              </Field>
            </>
          )}
          <Field label="WhatsApp / mobile" error={errors.phone} hint="Used for the morning brief and approvals by WhatsApp.">
            <Input type="tel" value={draft.phone} invalid={!!errors.phone} onChange={e => setDraft(d => ({ ...d, phone: e.target.value }))} />
          </Field>
          <Field label="Role" required hint={ROLE_HINTS[draft.role]} error={errors.role}>
            <Select value={draft.role} onChange={e => setDraft(d => ({ ...d, role: e.target.value as Role }))}
                    options={ROLES.map(r => ({ value: r, label: ROLE_LABELS[r] }))} />
          </Field>
          <Field label="Departments" error={errors.departments} hint="Comma separated. Limits what a department head or staff member sees.">
            <Input value={draft.departments} list="cx-departments" onChange={e => setDraft(d => ({ ...d, departments: e.target.value }))} />
          </Field>
          <Field label="Floors" error={errors.floors} hint="Comma separated, e.g. 3, 4, 5. Leave empty for the whole hotel.">
            <Input value={draft.floors} onChange={e => setDraft(d => ({ ...d, floors: e.target.value }))} />
          </Field>
          {editing !== "new" && (
            <Field label="Access" htmlFor="cx-member-active">
              <label className="cx-row" style={{ gap: 8, fontSize: "0.88rem" }}>
                <input id="cx-member-active" type="checkbox" checked={draft.is_active}
                       onChange={e => setDraft(d => ({ ...d, is_active: e.target.checked }))} />
                Active (untick to suspend without deleting history)
              </label>
            </Field>
          )}
          <datalist id="cx-departments">{DEPARTMENT_SUGGESTIONS.map(d => <option key={d} value={d} />)}</datalist>
        </form>
      </Modal>

      <Modal
        open={removing !== null}
        onClose={() => setRemoving(null)}
        dismissible={!saving}
        size="sm"
        title="Remove from this hotel?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)} disabled={saving}>Cancel</Button>
            <Button variant="danger" loading={saving} onClick={remove}>Remove</Button>
          </>
        }
      >
        <p className="cx-muted" style={{ margin: 0, lineHeight: 1.55 }}>
          {removing?.name} will lose access to this hotel immediately. Their past actions stay in the audit log.
        </p>
      </Modal>
    </div>
  );
}
