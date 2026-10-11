"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Guest profiles (consent-gated: a profile exists only while the guest's
   "remember me" and data-transfer consents stand). Names are sealed on the
   server, so there is no name search: staff find a guest by their exact
   phone or email, which the server hashes and never stores.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Input, Segmented, Table, type Column } from "../../../../../ui";
import { gstApi } from "../lib";
import type { Profile } from "../types";
import ProfileDrawer from "./ProfileDrawer";

type Filter = "all" | "returning" | "vip";
const PAGE = 50;

export default function ProfilesView({ initialId, initialFilter }: { initialId: number | null; initialFilter: string }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [filter, setFilter] = useState<Filter>((initialFilter as Filter) || "all");
  const [contactDraft, setContactDraft] = useState("");
  const [contact, setContact] = useState("");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<number | null>(initialId);

  const query = {
    contact: contact || undefined,
    vip: filter === "vip" ? true : undefined,
    returning: filter === "returning" ? true : undefined,
    limit: PAGE,
    offset,
  };
  const res = useApi<{ items: Profile[]; total: number }>(withQuery(gstApi(slug, "/profiles"), query), [slug, query]);
  const rows = res.data?.items ?? [];
  const total = res.data?.total ?? 0;

  const columns: Column<Profile>[] = [
    { key: "name", header: "Guest", render: p => (
      <span className="cx-row" style={{ gap: 6 }}>
        <strong>{p.first_name || "Guest"}</strong>
        {p.vip && <Badge tone="gold">VIP</Badge>}
        {p.returning && <Badge tone="info">Returning</Badge>}
      </span>
    ) },
    { key: "stays", header: "Stays", align: "right", render: p => <span className="gst-num">{p.stays_count}</span> },
    { key: "last", header: "Last seen", render: p => fmt.date(p.last_seen) },
    { key: "contact", header: "Contact", render: p => (
      <span className="gst-small gst-muted">
        {[p.has_phone && "Phone", p.has_email && "Email"].filter(Boolean).join(" + ") || "None"}
      </span>
    ) },
    { key: "tags", header: "Preferences", hideOnMobile: true, render: p => (
      <span className="gst-small">{p.preferences?.tags?.slice(0, 3).join(", ") || <span className="gst-faint">-</span>}</span>
    ) },
    { key: "lang", header: "Lang", hideOnMobile: true, render: p => (p.language || "en").toUpperCase() },
  ];

  function search(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    setContact(contactDraft.trim());
  }

  return (
    <Card
      title="Guest profiles"
      subtitle="Only guests who agreed to be remembered. Details are masked for your role."
    >
      <form className="gst-toolbar" onSubmit={search} style={{ marginBottom: 12 }}>
        <Segmented<Filter>
          ariaLabel="Filter profiles"
          size="sm"
          value={filter}
          onChange={v => { setFilter(v); setOffset(0); }}
          options={[{ value: "all", label: "All" }, { value: "returning", label: "Returning" }, { value: "vip", label: "VIP" }]}
        />
        <div className="gst-grow">
          <Input
            aria-label="Exact phone or email"
            placeholder="Exact phone (with country code) or email"
            value={contactDraft}
            onChange={e => setContactDraft(e.target.value)}
            autoComplete="off"
          />
        </div>
        <Button size="sm" type="submit">Find</Button>
        {contact && (
          <Button size="sm" variant="ghost" type="button" onClick={() => { setContact(""); setContactDraft(""); }}>Clear</Button>
        )}
      </form>

      <Table
        columns={columns}
        rows={rows}
        rowKey={p => p.id}
        onRowClick={p => setOpenId(p.id)}
        loading={res.loading && !res.data}
        empty={res.error ? res.error.message : contact ? "No profile for that contact." : "No profiles yet. Profiles appear when a guest agrees to be remembered."}
        caption="Guest profiles"
      />

      {total > PAGE && (
        <div className="cx-row" style={{ justifyContent: "space-between", marginTop: 10 }}>
          <span className="gst-small gst-muted">
            {offset + 1} to {Math.min(offset + PAGE, total)} of {fmt.number(total)}
          </span>
          <span className="cx-row">
            <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
            <Button size="sm" variant="ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
          </span>
        </div>
      )}

      <ProfileDrawer
        profileId={openId}
        onClose={() => setOpenId(null)}
        onChanged={() => res.reload()}
      />
    </Card>
  );
}
