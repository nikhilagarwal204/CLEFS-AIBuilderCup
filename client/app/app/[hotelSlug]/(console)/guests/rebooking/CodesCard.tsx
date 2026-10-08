"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Direct-booking codes. Issuing one sends nothing: staff hand it over or
   the J8 step includes it. A redeemed code on a new direct reservation is
   what the direct_rebook_conversions metric counts, so the list shows the
   redeeming reservation next to each code.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Field, Input, Table, useToast, type Column } from "../../../../../ui";
import { errText, gstApi, PERM } from "../lib";
import type { DirectCode } from "../types";

export default function CodesCard() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<DirectCode[]>(gstApi(slug, "/codes"), [slug]);
  const [validTo, setValidTo] = useState("");
  const [pct, setPct] = useState("");
  const [prefix, setPrefix] = useState("");
  const [profileId, setProfileId] = useState("");
  const [saving, setSaving] = useState(false);

  async function issue(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const code = await api.post<DirectCode>(gstApi(slug, "/codes"), {
        valid_to: validTo,
        discount_pct: pct ? Number(pct) : undefined,
        prefix: prefix.trim() || undefined,
        profile_id: profileId ? Number(profileId) : undefined,
      });
      toast.success(`Code ${code.code} issued`, "Nothing was sent. Share it with the guest yourself.");
      setPct(""); setPrefix(""); setProfileId("");
      res.reload();
    } catch (err) {
      toast.error("Could not issue the code", errText(err));
    } finally {
      setSaving(false);
    }
  }

  const cols: Column<DirectCode>[] = [
    { key: "code", header: "Code", render: c => <span className="gst-mono">{c.code}</span> },
    { key: "pct", header: "Discount", align: "right", render: c => (c.discount_pct != null ? `${c.discount_pct}%` : "-") },
    { key: "to", header: "Valid to", render: c => fmt.date(c.valid_to) },
    { key: "src", header: "From", hideOnMobile: true, render: c => (c.plan_id ? "Rebooking offer" : c.offer_id ? "Offer" : "Issued by staff") },
    { key: "state", header: "Status", render: c => c.redeemed_at
      ? <Badge tone="ok" title={c.redeemed_reservation_id ? `Reservation #${c.redeemed_reservation_id}` : undefined}>Redeemed {fmt.date(c.redeemed_at)}</Badge>
      : new Date(c.valid_to) < new Date() ? <Badge>Expired</Badge> : <Badge tone="info">Open</Badge> },
  ];

  return (
    <Card title="Direct-booking codes" subtitle="Redeemed codes are the rebooking conversions the hotel can prove.">
      {can(PERM.manage) && (
        <form className="gst-toolbar" onSubmit={issue} style={{ marginBottom: 12 }}>
          <Field label="Valid to" htmlFor="gst-code-to" required>
            <Input id="gst-code-to" type="date" required value={validTo} onChange={e => setValidTo(e.target.value)} />
          </Field>
          <Field label="Discount %" htmlFor="gst-code-pct">
            <Input id="gst-code-pct" type="number" min={1} max={100} step="0.5" value={pct} onChange={e => setPct(e.target.value)} style={{ width: 100 }} />
          </Field>
          <Field label="Prefix" htmlFor="gst-code-prefix">
            <Input id="gst-code-prefix" maxLength={8} value={prefix} onChange={e => setPrefix(e.target.value.toUpperCase())} style={{ width: 110 }} />
          </Field>
          <Field label="Profile # (optional)" htmlFor="gst-code-prof">
            <Input id="gst-code-prof" type="number" min={1} value={profileId} onChange={e => setProfileId(e.target.value)} style={{ width: 130 }} />
          </Field>
          <Button type="submit" size="sm" loading={saving}>Issue code</Button>
        </form>
      )}
      <Table columns={cols} rows={res.data ?? []} rowKey={c => c.id} loading={res.loading && !res.data}
        empty={res.error ? res.error.message : "No codes issued yet."} caption="Direct-booking codes" />
    </Card>
  );
}
