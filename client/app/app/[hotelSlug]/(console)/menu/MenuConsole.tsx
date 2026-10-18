"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Menu console (upsell module, Canon 9): outlets, items in English and
   Arabic, pairings, CSV import, live orders and the upsell funnel.
   Outlets and items load once here and are shared by every tab, so an edit
   in Items shows up in Pairings and the phone-order basket without a
   second fetch. Tabs are gated with can(): the server stays the authority
   (403s still surface), the client only hides what would fail.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { hotelPath, useApi } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Button, Card, EmptyState, PageHeader, Tabs, type TabItem } from "../../../../ui";
import { upsStyles } from "./styles";
import type { MenuData, MenuItem, Outlet } from "./types";
import OrdersTab from "./OrdersTab";
import ItemsTab from "./ItemsTab";
import OutletsTab from "./OutletsTab";
import PairingsTab from "./PairingsTab";
import ImportTab from "./ImportTab";
import InsightsTab from "./InsightsTab";

// ─── Tabs ────────────────────────────────────────────────────────────────

type TabKey = "orders" | "items" | "outlets" | "pairings" | "import" | "insights";
const TAB_KEYS: TabKey[] = ["orders", "items", "outlets", "pairings", "import", "insights"];

export default function MenuConsole({ initialTab }: { initialTab?: string }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const canView = can(["menu.view", "menu.orders", "menu.manage"]);
  const canManage = can("menu.manage");
  const canOrders = can("menu.orders");

  // Deep link (?tab=items) so the Brief or a signal can open the right tab;
  // page.tsx reads it on the server, so no useSearchParams Suspense bailout.
  const [tab, setTab] = useState<TabKey>(
    initialTab && (TAB_KEYS as string[]).includes(initialTab) ? (initialTab as TabKey) : "orders",
  );

  const outletsRes = useApi<Outlet[]>(canView ? hotelPath(slug, "/menu/outlets") : null, [slug]);
  const itemsRes = useApi<MenuItem[]>(canView ? hotelPath(slug, "/menu/items") : null, [slug]);

  const data: MenuData = useMemo(() => ({
    slug,
    outlets: outletsRes.data ?? [],
    items: itemsRes.data ?? [],
    loading: outletsRes.loading || itemsRes.loading,
    canManage,
    canOrders,
    reloadOutlets: outletsRes.reload,
    reloadItems: itemsRes.reload,
  }), [slug, outletsRes.data, itemsRes.data, outletsRes.loading, itemsRes.loading, canManage, canOrders,
       outletsRes.reload, itemsRes.reload]);

  const tabs: TabItem<TabKey>[] = [
    { key: "orders", label: "Orders" },
    { key: "items", label: "Items", count: itemsRes.data?.length },
    { key: "outlets", label: "Outlets", count: outletsRes.data?.length },
    { key: "pairings", label: "Pairings" },
    { key: "import", label: "Import", disabled: !canManage },
    { key: "insights", label: "Upsell funnel" },
  ];

  const loadError = outletsRes.error || itemsRes.error;

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: upsStyles }} />
      <PageHeader
        eyebrow="Menu"
        title="Menus and orders"
        subtitle="What guests can order, when, and what they ordered. Prices show in your hotel currency."
      />
      {!canView ? (
        <Card>
          <EmptyState title="No access to the menu" body="Ask the owner or GM to give your role menu access." />
        </Card>
      ) : (
        <div className="cx-stack">
          <Tabs tabs={tabs} value={tab} onChange={setTab} ariaLabel="Menu sections" />
          {loadError && tab !== "orders" && tab !== "insights" ? (
            <Card>
              <EmptyState
                title="Could not load the menu"
                body={loadError.message}
                action={<Button onClick={() => { outletsRes.reload(); itemsRes.reload(); }}>Try again</Button>}
              />
            </Card>
          ) : (
            <>
              {tab === "orders" && <OrdersTab data={data} />}
              {tab === "items" && <ItemsTab data={data} />}
              {tab === "outlets" && <OutletsTab data={data} />}
              {tab === "pairings" && <PairingsTab data={data} />}
              {tab === "import" && <ImportTab data={data} />}
              {tab === "insights" && <InsightsTab data={data} />}
            </>
          )}
        </div>
      )}
    </>
  );
}
