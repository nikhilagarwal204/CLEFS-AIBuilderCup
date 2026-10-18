"use client";

/* ═══════════════════════════════════════════════════════════════════════
   WhatsApp opt-in (02 spec 6.2, "Contacts: opt-in capture"). One row per
   member who may receive the brief, with the number masked. Anyone may
   opt themselves in or out; changing someone else needs brief.manage.
   A member with no row is "implicit" (they gave Clefs the number and the
   hotel lists WhatsApp); an explicit opt-out always wins, and replying
   STOP on WhatsApp records the same thing. Audited server-side.
   A number is never typed in for anyone: a member adds their own by
   sending a one-time code from that WhatsApp (VerifyNumber), because the
   account's number signs them in and answers the brief's buttons.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi, type ApiError } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Table, useToast, type Column, type Tone } from "../../../../../ui";
import { brfApi, PERM, roleLabel } from "../lib";
import type { ContactState, WaContact } from "../types";
import VerifyNumber from "./VerifyNumber";

const STATE: Record<ContactState, { label: string; tone: Tone }> = {
  implicit: { label: "On (account number)", tone: "info" },
  opted_in: { label: "Opted in", tone: "ok" },
  opted_out: { label: "Opted out", tone: "warn" },
  no_phone: { label: "No number", tone: "neutral" },
};

const ERR: Record<string, string> = {
  phone_mismatch: "That is not the number on this account. Change it in the team settings first.",
  phone_unverified: "Confirm the number from its WhatsApp first.",
  no_phone: "Add a WhatsApp number first.",
  member_not_found: "That person is no longer a member here.",
};

export default function WaContacts() {
  const { slug } = useHotel();
  const { can, user } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = can(PERM.manage);
  const list = useApi<WaContact[]>(brfApi(slug, "/wa/contacts"), [slug]);
  const [busy, setBusy] = useState<number | null>(null);
  const [verifying, setVerifying] = useState(false);

  const onVerified = () => {
    toast.success("Number confirmed", "The brief will come to your WhatsApp.");
    list.reload();
  };

  const post = async (c: WaContact, optedIn: boolean) => {
    setBusy(c.user_id);
    try {
      const body: Record<string, unknown> = { opted_in: optedIn };
      if (c.user_id !== user?.id) body.user_id = c.user_id;
      const out = await api.post<WaContact>(brfApi(slug, "/wa/contacts"), body);
      list.setData(prev => (prev ?? []).map(r => (r.user_id === out.user_id ? out : r)));
      toast.success(optedIn ? "WhatsApp brief on" : "WhatsApp brief off", out.name);
    } catch (e) {
      const ae = e as ApiError;
      toast.error("Not changed", ERR[ae.code ?? ""] ?? ae.message);
    } finally {
      setBusy(null);
    }
  };

  const mayEdit = (c: WaContact) => canManage || c.user_id === user?.id;

  const columns: Column<WaContact>[] = [
    { key: "name", header: "Member", render: c => (
      <div style={{ display: "grid", gap: 2 }}>
        <span style={{ color: "var(--clefs-ink)", fontWeight: 600 }}>{c.name}{c.user_id === user?.id ? " (you)" : ""}</span>
        <span className="brf-muted">{roleLabel(String(c.role))}</span>
      </div>
    ) },
    { key: "phone", header: "WhatsApp", render: c => c.phone_masked ?? "-" },
    { key: "state", header: "Brief on WhatsApp", render: c => (
      <div style={{ display: "grid", gap: 2 }}>
        <Badge tone={STATE[c.state].tone}>{STATE[c.state].label}</Badge>
        {c.state === "opted_out" && c.opted_out_at && (
          <span className="brf-muted">{c.opt_out_method === "whatsapp_stop" ? "Replied STOP" : "Turned off"} {fmt.date(c.opted_out_at, "medium")}</span>
        )}
      </div>
    ) },
    { key: "act", header: "", align: "right", render: c => {
      if (!mayEdit(c)) return null;
      if (c.state === "no_phone") {
        // Only the member can add their own number; a manager can only ask them to.
        return c.user_id === user?.id
          ? <Button size="sm" variant="secondary" onClick={() => setVerifying(true)}>Add number</Button>
          : <span className="brf-muted">They add their own number</span>;
      }
      const on = c.state !== "opted_out";
      return (
        <Button size="sm" variant={on ? "ghost" : "secondary"} loading={busy === c.user_id} disabled={busy !== null}
                onClick={() => void post(c, !on)}>{on ? "Turn off" : "Turn on"}</Button>
      );
    } },
  ];

  return (
    <Card title="WhatsApp brief" subtitle="Who gets the brief on WhatsApp. Replying STOP turns it off; START turns it back on.">
      {list.error && !list.data ? (
        <EmptyState title="Could not load members" body={list.error.message} action={<Button onClick={list.reload}>Try again</Button>} />
      ) : (
        <Table columns={columns} rows={list.data ?? []} rowKey={c => c.user_id} loading={list.loading && !list.data}
               caption="WhatsApp brief recipients" empty="No members who can read the brief yet." />
      )}
      {verifying && <VerifyNumber slug={slug} open onClose={() => setVerifying(false)} onVerified={onVerified} />}
    </Card>
  );
}
