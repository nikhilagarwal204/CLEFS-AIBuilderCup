"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Reviews console (reviews module, 03 spec 4.1): one feed across Google,
   Tripadvisor, OTAs and forwarded emails; what guests complain about and
   whether it is getting worse; drafted replies to approve, post or copy;
   where reviews come from; and how the neighbours are rated.

   The summary tiles and the review drawer live here so every tab shares
   them: approving a reply in the drawer refreshes the tiles and the tab
   that opened it, without each tab re-implementing the reply flow. Tabs are
   gated with can(); the server stays the authority (a 403 still surfaces).
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useState } from "react";
import { hotelPath, useApi } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Card, EmptyState, PageHeader, Tabs, useToast, type TabItem } from "../../../../ui";
import { revStyles } from "./styles";
import type { Summary } from "./types";
import SummaryTiles from "./SummaryTiles";
import FeedTab from "./FeedTab";
import RepliesTab from "./RepliesTab";
import InsightsTab from "./InsightsTab";
import SourcesTab from "./SourcesTab";
import CompetitorsTab from "./CompetitorsTab";
import ReviewDrawer from "./ReviewDrawer";

// ─── Tabs ────────────────────────────────────────────────────────────────

type TabKey = "feed" | "replies" | "insights" | "sources" | "competitors";
const TAB_KEYS: TabKey[] = ["feed", "replies", "insights", "sources", "competitors"];

export interface OAuthReturn { status: string; error?: string }

export default function ReviewsConsole({ initialTab, oauthReturn }: {
  initialTab?: string;
  oauthReturn?: OAuthReturn;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const canView = can(["reviews.view", "reviews.manage"]);
  const canManage = can("reviews.manage");

  // A return from Google's consent screen lands on the Sources tab.
  const [tab, setTab] = useState<TabKey>(() => {
    if (oauthReturn) return "sources";
    return initialTab && (TAB_KEYS as string[]).includes(initialTab) ? (initialTab as TabKey) : "feed";
  });
  const [openId, setOpenId] = useState<number | null>(null);
  // Bumped after any reply or review change so the open tab refetches.
  const [version, setVersion] = useState(0);

  const summary = useApi<Summary>(canView ? hotelPath(slug, "/reviews/summary?days=30") : null, [slug, version]);

  const changed = useCallback(() => setVersion(v => v + 1), []);

  // One toast for the OAuth round trip, then strip the params from the URL so
  // a reload does not repeat it. history.replaceState keeps the Next router
  // state intact (no navigation, no refetch).
  useEffect(() => {
    if (!oauthReturn) return;
    if (oauthReturn.status === "connected") toast.success("Google connected", "Reviews will sync from Google Business Profile.");
    else if (oauthReturn.status === "denied") toast.info("Google not connected", "The permission request was declined.");
    else toast.error("Google connection failed", oauthReturn.error ? `Reason: ${oauthReturn.error}` : undefined);
    try {
      const url = new URL(window.location.href);
      ["oauth", "oauth_status", "oauth_error"].forEach(k => url.searchParams.delete(k));
      url.searchParams.set("tab", "sources");
      window.history.replaceState(window.history.state, "", url.toString());
    } catch { /* cosmetic only */ }
    // Once per mount: the params are consumed on first render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tabs: TabItem<TabKey>[] = [
    { key: "feed", label: "Feed" },
    { key: "replies", label: "Replies", count: summary.data?.drafts_waiting || undefined },
    { key: "insights", label: "Themes and trends" },
    { key: "sources", label: "Sources" },
    { key: "competitors", label: "Competitors" },
  ];

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: revStyles }} />
      <PageHeader
        eyebrow="Reviews"
        title="Guest reviews"
        subtitle="Every review in one place, what guests keep mentioning, and replies drafted for you to approve."
      />
      {!canView ? (
        <Card>
          <EmptyState title="No access to reviews" body="Ask the owner or GM to give your role reviews access." />
        </Card>
      ) : (
        <div className="cx-stack">
          <SummaryTiles summary={summary} />
          <Tabs tabs={tabs} value={tab} onChange={setTab} ariaLabel="Reviews sections" />
          {tab === "feed" && <FeedTab slug={slug} version={version} onOpen={setOpenId} />}
          {tab === "replies" && <RepliesTab slug={slug} version={version} onOpen={setOpenId} />}
          {tab === "insights" && <InsightsTab slug={slug} />}
          {tab === "sources" && <SourcesTab slug={slug} canManage={canManage} onChanged={changed} />}
          {tab === "competitors" && <CompetitorsTab slug={slug} canManage={canManage} />}
        </div>
      )}
      <ReviewDrawer
        slug={slug}
        reviewId={openId}
        canManage={canManage}
        onClose={() => setOpenId(null)}
        onChanged={changed}
      />
    </>
  );
}
