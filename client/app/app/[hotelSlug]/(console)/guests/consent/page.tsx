"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Consent desk. Look up one guest's consent by their exact phone or email
   (sent in a POST body so the value never lands in a URL or access log),
   record an opt-out, or erase the contact (PDPL right to erasure). Below
   it, the registration-card capture that turns an OTA guest into a direct
   contact with evidence (Canon 19).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import Link from "next/link";
import { api } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Input, Segmented, Table, useToast, type Column } from "../../../../../ui";
import ConsentCard from "../ConsentCard";
import { errText, gstApi, gstHref, humanize, PERM, PROVENANCE_LABEL } from "../lib";
import type { ConsentLookup, ConsentPoint, ContactKind } from "../types";
import CaptureCard from "./CaptureCard";

export default function ConsentPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const [kind, setKind] = useState<ContactKind>("phone");
  const [value, setValue] = useState("");
  const [asked, setAsked] = useState<{ kind: ContactKind; value: string } | null>(null);
  const [result, setResult] = useState<ConsentLookup | null>(null);
  const [busy, setBusy] = useState<"" | "lookup" | "optout" | "forget">("");

  async function lookup(e?: React.FormEvent) {
    e?.preventDefault();
    const v = value.trim();
    if (!v) return;
    setBusy("lookup");
    try {
      setResult(await api.post<ConsentLookup>(gstApi(slug, "/consents/lookup"), { kind, value: v }));
      setAsked({ kind, value: v });
    } catch (err) {
      setResult(null);
      toast.error("Lookup failed", errText(err));
    } finally {
      setBusy("");
    }
  }

  async function optOut(scope: "all" | "marketing") {
    if (!asked) return;
    const what = scope === "all" ? "all messages" : "offers only";
    if (!window.confirm(`Stop ${what} to this ${asked.kind}? The guest can opt in again only with a fresh, recorded yes.`)) return;
    setBusy("optout");
    try {
      await api.post(gstApi(slug, "/opt-out"), { ...asked, scope });
      toast.success("Opt-out recorded");
      await lookup();
    } catch (err) {
      toast.error("Could not record the opt-out", errText(err));
    } finally {
      setBusy("");
    }
  }

  async function forget() {
    if (!asked) return;
    if (!window.confirm("Erase this contact? Stored numbers and emails, the profile and any card evidence are deleted. A STOP is kept so they are never messaged again. This cannot be undone.")) return;
    setBusy("forget");
    try {
      const r = await api.post<{ points_purged: number; profiles_deleted: number; evidence_files_deleted: number }>(
        gstApi(slug, "/forget"), asked);
      toast.success("Contact erased", `${r.points_purged} contact records, ${r.profiles_deleted} profiles, ${r.evidence_files_deleted} evidence files.`);
      setResult(null);
      setAsked(null);
      setValue("");
    } catch (err) {
      toast.error("Could not erase", errText(err));
    } finally {
      setBusy("");
    }
  }

  const pointCols: Column<ConsentPoint>[] = [
    { key: "prov", header: "Where it came from", render: p => PROVENANCE_LABEL[p.provenance] ?? humanize(p.provenance) },
    { key: "src", header: "Captured by", render: p => humanize(p.capture_source) },
    { key: "status", header: "Status", render: p => <Badge tone={p.status === "active" ? "ok" : "neutral"}>{humanize(p.status)}</Badge> },
    { key: "until", header: "Usable until", render: p => (p.usable_until ? fmt.date(p.usable_until) : "Open") },
    { key: "res", header: "Booking", hideOnMobile: true, render: p => (p.reservation_id != null ? (
      can(PERM.jView)
        ? <Link href={gstHref(slug, `/journeys/timeline/${p.reservation_id}`)}>#{p.reservation_id}</Link>
        : `#${p.reservation_id}`
    ) : "-") },
    { key: "at", header: "Added", hideOnMobile: true, render: p => fmt.date(p.created_at) },
  ];

  return (
    <>
      {can(PERM.view) && (
        <Card title="Look up a guest's consent" subtitle="Exact phone or email. It is hashed for the lookup and never stored.">
          <form className="gst-toolbar" onSubmit={lookup}>
            <Segmented<ContactKind> ariaLabel="Contact type" size="sm" value={kind} onChange={setKind}
              options={[{ value: "phone", label: "Phone" }, { value: "email", label: "Email" }]} />
            <div className="gst-grow">
              <Input aria-label={kind === "phone" ? "Phone number" : "Email address"} value={value} autoComplete="off"
                inputMode={kind === "phone" ? "tel" : "email"} placeholder={kind === "phone" ? "+971 50 123 4567" : "guest@example.com"}
                onChange={e => setValue(e.target.value)} />
            </div>
            <Button size="sm" type="submit" loading={busy === "lookup"}>Look up</Button>
          </form>

          {result && (
            <div className="cx-stack" style={{ marginTop: 14, gap: 12 }}>
              <ConsentCard view={result} />
              <Table columns={pointCols} rows={result.points} rowKey={p => p.id} caption="Contact records"
                empty="The hotel holds no record of this contact." />
              <div className="cx-row" style={{ justifyContent: "space-between" }}>
                {result.profile_id != null
                  ? <Link className="gst-small" href={gstHref(slug, `/profiles?id=${result.profile_id}`)}>Open profile</Link>
                  : <span className="gst-small gst-muted">No profile (no &ldquo;remember me&rdquo; consent).</span>}
                {can(PERM.manage) && (
                  <span className="cx-row">
                    <Button size="sm" variant="secondary" loading={busy === "optout"} onClick={() => optOut("marketing")}>Stop offers</Button>
                    <Button size="sm" variant="secondary" loading={busy === "optout"} onClick={() => optOut("all")}>Stop all messages</Button>
                    <Button size="sm" variant="danger" loading={busy === "forget"} onClick={forget}>Erase contact</Button>
                  </span>
                )}
              </div>
            </div>
          )}
        </Card>
      )}

      {can(PERM.capture) && <CaptureCard />}
    </>
  );
}
