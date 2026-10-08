"use client";

/* ═══════════════════════════════════════════════════════════════════════
   AssignSheet: the staff picker inside ActionBar's "Assign to" dialog.

   Who can be picked: the brief's own member list (GET /brief/wa/contacts,
   brief.view: owners, GMs, finance, revenue and department heads), plus
   front desk and staff from the team list when the viewer may read it
   (core.team.manage). Core decide() re-checks membership, so a stale list
   can never assign outside the hotel. Mounted only while the dialog is
   open, so the lists load on demand, not once per card.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { hotelPath, useApi } from "../../../../lib/api";
import { useSession } from "../../../../lib/session";
import type { TeamMember } from "../../../../lib/types";
import { Button, EmptyState, Field, Input, Skeleton, Textarea, type AssignPickerApi } from "../../../../ui";
import { brfApi, PERM, roleLabel } from "./lib";
import type { WaContact } from "./types";

interface Person { user_id: number; name: string; role: string }

export default function AssignSheet({ slug, api, suggestUserId, suggestRole }: {
  slug: string;
  api: AssignPickerApi;
  /** The item's responsible person, listed first. */
  suggestUserId?: number | null;
  /** The item's owner role: members holding it are listed next. */
  suggestRole?: string | null;
}) {
  const { can } = useSession();
  const contacts = useApi<WaContact[]>(brfApi(slug, "/wa/contacts"), [slug]);
  const team = useApi<TeamMember[]>(can(PERM.team) ? hotelPath(slug, "/team") : null, [slug]);
  const [picked, setPicked] = useState<number | null>(suggestUserId ?? null);
  const [q, setQ] = useState("");
  const [note, setNote] = useState("");

  const people = useMemo<Person[]>(() => {
    const byId = new Map<number, Person>();
    for (const c of contacts.data ?? []) byId.set(c.user_id, { user_id: c.user_id, name: c.name, role: String(c.role) });
    for (const m of team.data ?? []) {
      if (m.user_id !== null && m.is_active && m.status !== "invited" && !byId.has(m.user_id)) {
        byId.set(m.user_id, { user_id: m.user_id, name: m.name, role: m.role });
      }
    }
    const rank = (p: Person) => (p.user_id === suggestUserId ? 0 : p.role === suggestRole ? 1 : 2);
    return [...byId.values()].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }, [contacts.data, team.data, suggestUserId, suggestRole]);

  const needle = q.trim().toLowerCase();
  const shown = needle
    ? people.filter(p => p.name.toLowerCase().includes(needle) || roleLabel(p.role).toLowerCase().includes(needle))
    : people;

  if (contacts.loading && !contacts.data) return <Skeleton lines={4} height={40} />;
  if (contacts.error && !people.length) {
    return <EmptyState title="Could not load the team" body={contacts.error.message}
                       action={<Button onClick={contacts.reload}>Try again</Button>} />;
  }

  return (
    <div className="cx-stack" style={{ gap: 12 }}>
      {people.length > 6 && (
        <Field label="Find a person">
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Name or role" autoFocus />
        </Field>
      )}
      {shown.length === 0 ? (
        <p className="brf-muted">No one matches.</p>
      ) : (
        <ul className="brf-pick" aria-label="Team members">
          {shown.map(p => (
            <li key={p.user_id}>
              <button type="button" aria-pressed={picked === p.user_id} onClick={() => setPicked(p.user_id)}>
                <span>{p.name}</span>
                <small>{p.user_id === suggestUserId ? "Responsible · " : ""}{roleLabel(p.role)}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Field label="Note (optional)" hint="Sent with the assignment.">
        <Textarea value={note} onChange={e => setNote(e.target.value)} rows={2} maxLength={2000} />
      </Field>
      <div className="brf-row" style={{ justifyContent: "flex-end" }}>
        <Button variant="ghost" onClick={api.close}>Cancel</Button>
        <Button variant="primary" disabled={picked === null}
                onClick={() => picked !== null && api.assign(picked, note.trim() || undefined)}>
          Assign
        </Button>
      </div>
    </div>
  );
}
