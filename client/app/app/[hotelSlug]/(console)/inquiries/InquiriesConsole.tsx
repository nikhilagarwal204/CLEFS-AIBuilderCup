"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Inquiry Guard console (04 spec 4.11): every direct inquiry in one list,
   how long each has waited for a first reply, what it is worth (est.), and
   one drawer to read the thread, fix what extraction read, quote, take
   payment and close it with a reason.

   The list refetches on the hotel stream's `inquiries.changed` event, so a
   WhatsApp message arriving while the page is open shows without a reload.
   The weighted pipeline is the one headline number (4.10: no kanban).
   Permissions gate buttons with can(); the server stays the authority.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useState } from "react";
import { openHotelStream, useApi, withQuery } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import {
  Badge, Button, Card, CountdownChip, EmptyState, MoneyText, PageHeader, StatTile, Table, type Column,
} from "../../../../ui";
import Filters from "./Filters";
import InquiryDrawer from "./InquiryDrawer";
import SetupChecklist from "./SetupChecklist";
import {
  DEFAULT_FILTERS, filterQuery, inqPath, OPEN_STAGES, roomNights, SEGMENT_LABEL, slaState, SOURCE_LABEL,
  STAGE_LABEL, STAGE_TONE, type Filters as FilterValue,
} from "./lib";
import { inqStyles } from "./styles";
import type { Inquiry, Pipeline } from "./types";

export default function InquiriesConsole({ initialId, initialSla }: {
  initialId: number | null;
  initialSla: FilterValue["sla"];
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const canView = can("inquiries.view");
  const canRespond = can("inquiries.respond");
  const canManage = can("inquiries.manage");

  const [filters, setFilters] = useState<FilterValue>(() => ({ ...DEFAULT_FILTERS, sla: initialSla }));
  const [openId, setOpenId] = useState<number | null>(initialId);
  // Bumped by the stream and by any change made in the drawer.
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion(v => v + 1), []);

  const listPath = canView ? withQuery(inqPath(slug), filterQuery(filters)) : null;
  const list = useApi<Inquiry[]>(listPath, [version]);
  const pipeline = useApi<Pipeline>(canView ? inqPath(slug, "/pipeline") : null, [version]);
  // Overdue count for the tile: open inquiries past their first-reply SLA,
  // whatever the list filters say.
  const overdue = useApi<Inquiry[]>(
    canView ? withQuery(inqPath(slug), { sla: "breached", stage: OPEN_STAGES.join(","), limit: 500 }) : null,
    [version],
  );

  useEffect(() => {
    if (!canView) return;
    return openHotelStream(slug, { "inquiries.changed": bump }, { refetch: bump });
  }, [slug, canView, bump]);

  const rows = list.data ?? [];
  const overdueRows = overdue.data ?? [];
  const overdueAtRisk = overdueRows.reduce((s, r) => s + (r.money_at_risk_minor ?? 0), 0);

  const fmt = useFormat();
  const columns = useMemo<Column<Inquiry>[]>(() => [
    {
      key: "ref", header: "Ref",
      render: r => (
        <span>
          <span className="inq-ref">{r.ref.toUpperCase()}</span>
          <span className="inq-sub">{r.contact_name || r.company || (r.has_phone || r.has_email ? "Guest" : "No contact")}</span>
        </span>
      ),
    },
    { key: "received", header: "Received", render: r => <span title={fmt.dateTime(r.created_at, "medium")}>{fmt.relative(r.created_at)}</span> },
    { key: "source", header: "Source", render: r => SOURCE_LABEL[r.source] ?? r.source, hideOnMobile: true },
    { key: "segment", header: "Segment", render: r => SEGMENT_LABEL[r.segment] ?? r.segment, hideOnMobile: true },
    {
      key: "dates", header: "Dates",
      render: r => (r.check_in
        ? <span>{fmt.date(`${r.check_in}T12:00:00Z`)}{r.check_out ? ` to ${fmt.date(`${r.check_out}T12:00:00Z`)}` : ""}
          {r.dates_flexible && <span className="inq-sub">flexible</span>}</span>
        : <span className="inq-muted">Not given</span>),
    },
    {
      key: "rn", header: "Room-nights", align: "right",
      render: r => {
        const rn = roomNights(r);
        return rn.value === null ? "-" : <span>{rn.value}{rn.estimated && <span className="inq-est">est.</span>}</span>;
      },
    },
    {
      key: "risk", header: "At risk (est.)", align: "right",
      render: r => (r.money_at_risk_minor == null ? "-" : <MoneyText amount={r.money_at_risk_minor} currency={r.currency} minor compact />),
    },
    {
      key: "sla", header: "First reply",
      render: r => {
        const s = slaState(r);
        if (s === "answered") return <Badge tone="ok">Replied</Badge>;
        if (s === "none") return <span className="inq-muted">-</span>;
        return <CountdownChip due={r.first_response_due_at} warnWithinHours={1} />;
      },
    },
    { key: "stage", header: "Stage", render: r => <Badge tone={STAGE_TONE[r.stage]}>{STAGE_LABEL[r.stage] ?? r.stage}</Badge> },
  ], [fmt]);

  if (!canView) {
    return (
      <Card>
        <EmptyState title="No access to inquiries" body="Ask the owner or general manager to give your role access." />
      </Card>
    );
  }

  return (
    <div>
      <style dangerouslySetInnerHTML={{ __html: inqStyles }} />
      <PageHeader
        eyebrow="Inquiry Guard"
        title="Inquiries"
        subtitle="Direct booking requests from WhatsApp, email, your website and lead ads. Reply fast, quote inside your guardrails, and see which ones became verified bookings."
        actions={
          <>
            {canRespond && <Button variant="primary" href={`/app/${slug}/inquiries/new`}>Log a phone inquiry</Button>}
            {canManage && <Button href={`/app/${slug}/inquiries/settings`}>Settings</Button>}
          </>
        }
      />
      <div className="inq-head-stats">
        <StatTile
          label="Weighted pipeline"
          loading={pipeline.loading && !pipeline.data}
          value={pipeline.data
            ? <><MoneyText amount={pipeline.data.total_minor} currency={pipeline.data.currency} minor compact /><span className="inq-est">est.</span></>
            : "Not available"}
          sub={pipeline.data ? `${pipeline.data.count} open, arriving from today` : pipeline.error?.message}
        />
        <StatTile
          label="Overdue first replies"
          loading={overdue.loading && !overdue.data}
          value={String(overdueRows.length)}
          sub={overdueRows.length
            ? <><MoneyText amount={overdueAtRisk} minor compact /> at risk <span className="inq-est">est.</span></>
            : "Nobody is waiting past the SLA"}
        />
        <StatTile label="In this view" value={String(rows.length)} loading={list.loading && !list.data}
          sub={rows.length >= 200 ? "Showing the newest 200" : undefined} />
        <PipelineByMonth data={pipeline.data} />
      </div>
      <Filters value={filters} onChange={setFilters} />
      {list.error ? (
        <Card><EmptyState title="Could not load inquiries" body={list.error.message}
          action={<Button onClick={list.reload}>Try again</Button>} /></Card>
      ) : !list.loading && rows.length === 0 && filtersAreDefault(filters) && overdueRows.length === 0 ? (
        <SetupChecklist canManage={canManage} />
      ) : (
        <Card padded={false}>
          <Table
            columns={columns}
            rows={rows}
            rowKey={r => r.id}
            onRowClick={r => setOpenId(r.id)}
            loading={list.loading && !list.data}
            caption="Inquiries"
            empty="No inquiries match these filters."
          />
        </Card>
      )}
      <InquiryDrawer
        id={openId}
        onClose={() => setOpenId(null)}
        onChanged={bump}
        onOpen={setOpenId}
      />
    </div>
  );
}

// ─── Pieces ──────────────────────────────────────────────────────────────

/** The empty-state checklist shows only on the untouched default view: an
    empty result under filters someone chose is "no match", not "no setup". */
function filtersAreDefault(f: FilterValue): boolean {
  return JSON.stringify(f) === JSON.stringify(DEFAULT_FILTERS);
}

/** Next three check-in months of the weighted pipeline (est.), as text:
    three numbers read better than a chart this small. */
function PipelineByMonth({ data }: { data: Pipeline | undefined }) {
  const fmt = useFormat();
  const months = (data?.months ?? []).slice(0, 3);
  return (
    <StatTile
      label="By check-in month (est.)"
      value={months.length ? "" : "-"}
      sub={months.length ? (
        <span className="inq-row" style={{ gap: 12 }}>
          {months.map(m => (
            <span key={m.month}>
              {monthLabel(m.month, fmt.locale)}
              {": "}<MoneyText amount={m.weighted_minor} currency={data?.currency} minor compact />
            </span>
          ))}
        </span>
      ) : "No open inquiries with dates"}
    />
  );
}

/** "2026-10" or "2026-10-01" -> "Oct 2026" (UTC noon, so no timezone can
    shift it into the neighbouring month). */
function monthLabel(month: string, locale: string): string {
  const d = new Date(`${month.slice(0, 7)}-01T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return month;
  try {
    return new Intl.DateTimeFormat(locale, { month: "short", year: "numeric", timeZone: "UTC" }).format(d);
  } catch {
    return month.slice(0, 7);
  }
}
