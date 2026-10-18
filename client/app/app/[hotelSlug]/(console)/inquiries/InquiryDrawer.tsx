"use client";

/* One inquiry (4.11 drawer): a summary strip (stage, first-reply clock,
   money at risk est., the WhatsApp windows), then tabs for the thread and
   reply composer, what extraction read (with evidence, editable), the
   quote editor, payment links, and the verified or leaked outcome with its
   match reviews. Closing moves (lost, turned down, future, spam) sit in
   the footer behind one picker, so a reason is always recorded.

   Every child reports a change through `changed`, which refetches the
   detail here and bumps the list behind the drawer. */

import { useCallback, useState } from "react";
import { useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, CountdownChip, Drawer, EmptyState, MoneyText, Skeleton, Tabs, type TabItem } from "../../../../ui";
import ClosePicker from "./ClosePicker";
import DetailsPanel from "./DetailsPanel";
import OutcomePanel from "./OutcomePanel";
import PaymentPanel from "./PaymentPanel";
import QuotePanel from "./QuotePanel";
import ThreadPanel from "./ThreadPanel";
import { inqPath, OPEN_STAGES, reasonLabel, SOURCE_LABEL, STAGE_LABEL, STAGE_TONE, slaState, windowOpen } from "./lib";
import type { InquiryDetail } from "./types";

type TabKey = "thread" | "details" | "quote" | "payments" | "outcome";

export default function InquiryDrawer({ id, onClose, onChanged, onOpen }: {
  id: number | null;
  onClose: () => void;
  onChanged: () => void;
  onOpen: (id: number) => void;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const [tab, setTab] = useState<TabKey>("thread");
  const [version, setVersion] = useState(0);
  const [closing, setClosing] = useState(false);
  const detail = useApi<InquiryDetail>(id ? inqPath(slug, `/${id}`) : null, [version]);

  const changed = useCallback(() => {
    setVersion(v => v + 1);
    onChanged();
  }, [onChanged]);

  const row = detail.data && detail.data.id === id ? detail.data : undefined;
  const isOpen = row ? (OPEN_STAGES as string[]).includes(row.stage) : false;

  const tabs: TabItem<TabKey>[] = [
    { key: "thread", label: "Conversation", count: row?.messages.length },
    { key: "details", label: "Details" },
    { key: "quote", label: "Quote", disabled: !can(["inquiries.quote", "inquiries.view"]) },
    { key: "payments", label: "Payments" },
    { key: "outcome", label: "Outcome" },
  ];

  const title = row
    ? <span>{row.ref.toUpperCase()} <span className="inq-muted">{row.contact_name || row.company || ""}</span></span>
    : "Inquiry";

  const footer = row && can("inquiries.respond") ? (
    <div className="inq-row inq-row--between" style={{ width: "100%" }}>
      <span className="inq-muted">{isOpen ? "Close with a reason when it will not book." : `Closed: ${STAGE_LABEL[row.stage]}`}</span>
      {isOpen && <Button variant="secondary" onClick={() => setClosing(true)}>Close inquiry</Button>}
    </div>
  ) : undefined;

  return (
    <Drawer open={id !== null} onClose={() => { setTab("thread"); onClose(); }} title={title} footer={footer} wide>
      {!row && detail.loading && <Skeleton lines={6} height={16} />}
      {!row && detail.error && (
        <EmptyState title={detail.error.status === 404 ? "Inquiry not found" : "Could not load this inquiry"}
          body={detail.error.message} action={<Button onClick={detail.reload}>Try again</Button>} />
      )}
      {row && (
        <>
          <div className="inq-row" style={{ marginBottom: 12 }}>
            <Badge tone={STAGE_TONE[row.stage]}>{STAGE_LABEL[row.stage]}</Badge>
            <Badge>{SOURCE_LABEL[row.source] ?? row.source}</Badge>
            {row.is_test && <Badge tone="info">Test lead</Badge>}
            {row.lost_reason && <Badge>{reasonLabel(row.lost_reason)}</Badge>}
            {row.stage === "future" && row.future_remind_on && (
              <Badge tone="info">Remind {fmt.date(`${row.future_remind_on}T12:00:00Z`, "medium")}</Badge>
            )}
            {slaState(row) === "answered"
              ? <Badge tone="ok" title={row.first_response_by ?? undefined}>
                  Replied {row.first_response_business_min != null ? `in ${fmt.mins(row.first_response_business_min)}` : ""}
                </Badge>
              : row.first_response_due_at && isOpen
                ? <CountdownChip due={row.first_response_due_at} warnWithinHours={1} prefix="Reply" />
                : null}
            {row.money_at_risk_minor != null && isOpen && (
              <Badge tone="gold" title="Room-nights x expected rate x chance of booking at this stage">
                <MoneyText amount={row.money_at_risk_minor} currency={row.currency} minor /> at risk est.
              </Badge>
            )}
            {row.source === "whatsapp" || row.wa_window_expires_at ? (
              windowOpen(row.wa_window_expires_at)
                ? <CountdownChip due={row.wa_window_expires_at} warnWithinHours={2} prefix="WhatsApp window" />
                : <Badge title="Outside 24 h since the guest's last message only an approved template can go">WhatsApp: template only</Badge>
            ) : null}
            {windowOpen(row.ctwa_free_window_expires_at) && (
              <CountdownChip due={row.ctwa_free_window_expires_at} warnWithinHours={6} prefix="Free ad window" />
            )}
          </div>
          <Tabs tabs={tabs} value={tab} onChange={setTab} ariaLabel="Inquiry sections" />
          <div className="inq-tabs-body">
            {tab === "thread" && <ThreadPanel row={row} onChanged={changed} />}
            {tab === "details" && <DetailsPanel row={row} onChanged={changed} onOpen={onOpen} />}
            {tab === "quote" && <QuotePanel row={row} onChanged={changed} />}
            {tab === "payments" && <PaymentPanel row={row} onChanged={changed} />}
            {tab === "outcome" && <OutcomePanel row={row} onChanged={changed} />}
          </div>
          <ClosePicker row={row} open={closing} onClose={() => setClosing(false)}
            onDone={() => { setClosing(false); changed(); }} />
        </>
      )}
    </Drawer>
  );
}
