"use client";

/* Settings > Imports: PMS exports in, through core/ingest.
   Most UAE independents have no PMS API Clefs can call, so the reservation
   list, folio charges and daily occupancy arrive as the CSV or Excel export
   the front office already makes. Upload once, confirm the columns once
   (the layout is remembered), and every later export of the same report
   maps itself. OTA statements live on Profit Guard, P&L files on Finance;
   this page is only for what the PMS prints. */

import { useState } from "react";
import { LuTrash2 } from "react-icons/lu";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, Drawer, EmptyState, Table, useToast, type Column } from "../../../../../ui";
import {
  BatchDetail, BatchList, errorText, IngestStyles, ingestPath, kindLabel, UploadDropzone, useCanImport,
  type Batch, type MappingTemplate,
} from "../../../../../ingest";

/* The kinds a PMS produces. BOOKING_ENGINE_ATTRIBUTION is the booking
   engine's UTM export: it rides here because the same front-office person
   pulls it, and it only enriches reservations the PMS export created. */
const PMS_KINDS = ["PMS_RESERVATIONS", "PMS_FOLIO", "OCCUPANCY", "BOOKING_ENGINE_ATTRIBUTION"] as const;

const KIND_HELP: Record<(typeof PMS_KINDS)[number], string> = {
  PMS_RESERVATIONS: "Arrivals, in-house and departures with rates and channel. Feeds occupancy, stays and OTA matching.",
  PMS_FOLIO: "Posted charges by transaction. Feeds minibar, F&B and extras revenue.",
  OCCUPANCY: "One row per day: rooms sold, available and out of order, if your PMS has a daily report.",
  BOOKING_ENGINE_ATTRIBUTION: "Booking reference with UTM source and campaign, to credit direct bookings.",
};

// ─── Saved layouts ───

function Templates({ refreshKey }: { refreshKey: number }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const canWrite = useCanImport();
  const res = useApi<MappingTemplate[]>(slug ? ingestPath(slug, "/templates") : null, [refreshKey]);
  const rows = (res.data ?? []).filter(t => (PMS_KINDS as readonly string[]).includes(String(t.kind ?? "")));

  const retire = async (t: MappingTemplate) => {
    if (!window.confirm("Forget this layout? The next export with these columns will ask you to map them again.")) return;
    try {
      await api.del(ingestPath(slug, `/templates/${t.id}`));
      res.setData(prev => (prev ?? []).filter(x => x.id !== t.id));
      toast.success("Layout forgotten");
    } catch (err) {
      toast.error("Couldn't remove the layout", errorText(err));
    }
  };

  const columns: Column<MappingTemplate>[] = [
    { key: "kind", header: "Report", render: t => kindLabel(String(t.kind ?? "")) },
    { key: "source", header: "Source", render: t => t.source_code },
    {
      key: "scope", header: "Scope",
      render: t => t.hotel_slug ? <Badge tone="gold">This hotel</Badge> : <Badge tone="neutral">Clefs default</Badge>,
    },
    { key: "uses", header: "Used", align: "right", render: t => <span className="cx-num">{String(t.uses ?? 0)}</span> },
    { key: "last", header: "Last used", hideOnMobile: true, render: t => (t.last_used_at ? fmt.relative(t.last_used_at) : "-") },
    {
      key: "x", header: <span className="cx-sr">Remove</span>, align: "right",
      render: t => (canWrite && t.hotel_slug
        ? <Button size="sm" variant="ghost" aria-label="Forget layout" icon={<LuTrash2 aria-hidden="true" />} onClick={() => void retire(t)} />
        : null),
    },
  ];

  return (
    <Card title="Remembered layouts" subtitle="Column mappings you confirmed. An export with the same columns maps itself.">
      {res.error
        ? <div className="ing-bad" role="alert">{res.error.message}</div>
        : <Table columns={columns} rows={rows} rowKey={t => t.id} loading={res.loading && !res.data}
                 empty={<EmptyState title="No layouts yet" body="Confirm the columns of your first export and it will be remembered here." />} />}
    </Card>
  );
}

// ─── Email-in address ───

function InboundAddress() {
  const { slug } = useHotel();
  const toast = useToast();
  const res = useApi<{ address: string | null; hints: string[] }>(slug ? ingestPath(slug, "/inbound-address") : null, [slug]);
  const address = res.data?.address;
  if (!address) return null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      toast.success("Address copied");
    } catch {
      toast.error("Couldn't copy", "Select the address and copy it by hand.");
    }
  };
  return (
    <Card title="Send exports by email" subtitle="Schedule your PMS to email its reports here; they land in the list above for review.">
      <div className="cx-row">
        <code className="cx-num" style={{ overflowWrap: "anywhere" }}>{address}</code>
        <Button size="sm" variant="ghost" onClick={copy}>Copy</Button>
      </div>
    </Card>
  );
}

// ─── Page ───

export default function ImportsSettingsPage() {
  const { can } = useSession();
  const canWrite = useCanImport();
  const [refresh, setRefresh] = useState(0);
  const [open, setOpen] = useState<Batch | null>(null);
  const bump = () => setRefresh(n => n + 1);

  if (!can("core.settings.view") && !canWrite) {
    return <EmptyState title="Imports are managed by the owner or GM" body="Ask them for access if you need to upload PMS exports." />;
  }

  return (
    <div className="cx-stack" style={{ marginTop: 18 }}>
      <IngestStyles />
      {canWrite && (
        <Card title="Upload a PMS export" subtitle="Reservations, folio charges, daily occupancy or booking-engine attribution.">
          <UploadDropzone kinds={PMS_KINDS} purpose="ingest.pms" onBatch={b => { setOpen(b); bump(); }} />
          <ul className="ing-note" style={{ margin: "12px 0 0", paddingLeft: 18 }}>
            {PMS_KINDS.map(k => <li key={k}><strong>{kindLabel(k)}:</strong> {KIND_HELP[k]}</li>)}
          </ul>
        </Card>
      )}

      <Card title="Import history" subtitle="Every PMS file and where it stands. Open one to map, review or import it.">
        <BatchList kinds={PMS_KINDS} refreshKey={refresh} onOpen={setOpen}
                   emptyBody="Upload your first PMS export above. Nothing is imported until you review it." />
      </Card>

      <Templates refreshKey={refresh} />
      <InboundAddress />

      <Drawer open={open !== null} onClose={() => setOpen(null)} wide
              title={open ? (open.filename || `Import #${open.id}`) : "Import"}>
        {open && (
          <BatchDetail key={open.id} batchId={open.id} onChange={bump} onClosed={() => { setOpen(null); bump(); }} />
        )}
      </Drawer>
    </div>
  );
}
