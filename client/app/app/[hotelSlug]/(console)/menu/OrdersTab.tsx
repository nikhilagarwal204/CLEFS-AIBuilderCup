"use client";

/* Orders: what guests (and the desk, for phone orders) have ordered, newest
   first. Refreshes every 30 s while the tab is visible: the module emits no
   SSE event of its own yet, and a kitchen screen left open must not show a
   stale "placed" for an order already delivered. Each order is also a
   Service request, so the F&B team works it from the Service queue; this
   tab is the money view (totals, payment, refunds). */

import { useEffect, useMemo, useState } from "react";
import { hotelPath, useApi, withQuery } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import {
  Badge, Button, Card, EmptyState, Field, Input, Segmented, Select, Table, type Column,
} from "../../../../ui";
import OrderDrawer from "./OrderDrawer";
import PhoneOrder from "./PhoneOrder";
import { OPEN_STATUSES, STATUS_LABEL, STATUS_TONE, paymentLabel } from "./lib";
import type { MenuData, Order } from "./types";

type View = "open" | "delivered" | "closed" | "all";
const VIEW_STATUS: Record<View, string | undefined> = {
  open: OPEN_STATUSES.join(","),
  delivered: "delivered",
  closed: "cancelled,refunded",
  all: undefined,
};

const POLL_MS = 30_000;

export default function OrdersTab({ data }: { data: MenuData }) {
  const fmt = useFormat();
  const [view, setView] = useState<View>("open");
  const [days, setDays] = useState("7");
  const [room, setRoom] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [phoneOrder, setPhoneOrder] = useState(false);

  const path = withQuery(hotelPath(data.slug, "/menu/orders"), {
    status: VIEW_STATUS[view], days, room: room.trim() || undefined, limit: 200,
  });
  const orders = useApi<Order[]>(path, [path]);
  const { reload } = orders;

  useEffect(() => {
    const tick = () => { if (document.visibilityState === "visible") reload(); };
    const id = window.setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [reload]);

  const rows = orders.data ?? [];
  const outletName = useMemo(() => {
    const m = new Map(data.outlets.map(o => [o.id, o.name_en]));
    return (id: number | null) => (id == null ? "" : m.get(id) ?? "");
  }, [data.outlets]);

  const openCount = rows.filter(o => OPEN_STATUSES.includes(o.status)).length;

  const columns: Column<Order>[] = [
    {
      key: "id", header: "Order",
      render: o => (
        <div>
          <div className="ups-item-name">#{o.order_id}</div>
          <div className="ups-sub" title={fmt.dateTime(o.created_at)}>{fmt.relative(o.created_at)}</div>
        </div>
      ),
    },
    {
      key: "room", header: "Room",
      render: o => (
        <div className="ups-tags">
          <span className="ups-item-name">{o.room}</span>
          {!o.verified_room && <Badge tone="warn" title="Ordered without the room's QR key: confirm at the door">Unverified</Badge>}
        </div>
      ),
    },
    {
      key: "items", header: "Items",
      render: o => (
        <div style={{ minWidth: 0 }}>
          <div>{o.lines.map(l => `${l.qty}x ${l.name}`).join(", ")}</div>
          <div className="ups-sub">{[outletName(o.outlet_id), o.source].filter(Boolean).join(" · ")}</div>
        </div>
      ),
    },
    {
      key: "total", header: "Total", align: "right",
      render: o => <span className="ups-price">{fmt.money(o.total_minor, { minor: true, currency: o.currency })}</span>,
    },
    { key: "status", header: "Status", render: o => <Badge tone={STATUS_TONE[o.status]} dot>{STATUS_LABEL[o.status]}</Badge> },
    {
      key: "paid", header: "Payment", hideOnMobile: true,
      render: o => <span className={o.paid_at ? undefined : "cx-faint"}>{o.paid_at ? paymentLabel(o.payment) : "Not recorded"}</span>,
    },
  ];

  return (
    <Card
      title="Orders"
      subtitle={view === "open" && !orders.loading ? `${openCount} open. Refreshes every 30 seconds.` : "Refreshes every 30 seconds."}
      actions={data.canOrders && <Button variant="primary" size="sm" onClick={() => setPhoneOrder(true)}>New phone order</Button>}
    >
      <div className="ups-toolbar">
        <Segmented ariaLabel="Order status" value={view} onChange={setView} options={[
          { value: "open", label: "Open" }, { value: "delivered", label: "Delivered" },
          { value: "closed", label: "Cancelled" }, { value: "all", label: "All" },
        ]} />
        <Field label="Period">
          <Select value={days} onChange={e => setDays(e.target.value)} options={[
            { value: "1", label: "Today" }, { value: "7", label: "Last 7 days" },
            { value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" },
          ]} />
        </Field>
        <Field label="Room">
          <Input value={room} onChange={e => setRoom(e.target.value)} placeholder="Any" inputMode="numeric" style={{ width: 110 }} />
        </Field>
      </div>
      {orders.error && !orders.data ? (
        <EmptyState title="Could not load orders" body={orders.error.message} action={<Button onClick={reload}>Try again</Button>} />
      ) : (
        <Table
          columns={columns}
          rows={rows}
          rowKey={o => o.id}
          loading={orders.loading && !orders.data}
          onRowClick={o => setOpenId(o.id)}
          empty={
            <EmptyState
              title={view === "open" ? "No open orders" : "No orders in this period"}
              body="Guest orders from the room QR menu and the concierge appear here, and on the Service queue as Food requests."
            />
          }
        />
      )}
      {openId !== null && (
        <OrderDrawer data={data} orderId={openId} onClose={() => setOpenId(null)} onChanged={reload} />
      )}
      {phoneOrder && (
        <PhoneOrder data={data} onClose={() => setPhoneOrder(false)}
                    onPlaced={o => { setPhoneOrder(false); reload(); setOpenId(o.id); }} />
      )}
    </Card>
  );
}
