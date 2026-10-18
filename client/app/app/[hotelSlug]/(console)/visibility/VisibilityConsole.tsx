"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Visibility console (04 spec 5.11): is the hotel's own price on Google,
   how do the OTAs undercut it, where does it rank, what do AI answer
   engines say about it, and is the website healthy.

   Every vendor number carries its source label (SerpApi is third-party,
   AI answers are API simulations) and every sampled rate its n and CI; a
   tab missing inputs shows a setup checklist, never an error. Tabs are
   gated with can(); the server stays the authority (a 403 still surfaces).
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { useSession } from "../../../../lib/session";
import { EmptyState, Card, PageHeader, Tabs, useToast, type TabItem } from "../../../../ui";
import { visStyles } from "./styles";
import GoogleTab from "./GoogleTab";
import AiTab from "./AiTab";
import RankTab from "./RankTab";
import SiteHealthTab from "./SiteHealthTab";
import CostsTab from "./CostsTab";
import SettingsTab from "./SettingsTab";

// ─── Tabs ────────────────────────────────────────────────────────────────

export type VisTab = "google" | "ai" | "rank" | "site" | "costs" | "settings";
const TAB_KEYS: VisTab[] = ["google", "ai", "rank", "site", "costs", "settings"];

export interface OAuthReturn { status: string; error?: string }

export default function VisibilityConsole({ initialTab, oauthReturn }: { initialTab?: string; oauthReturn?: OAuthReturn }) {
  const { can } = useSession();
  const toast = useToast();
  const canView = can(["visibility.view", "visibility.manage"]);

  // A return from Google's consent screen lands on Settings (the location picker).
  const [tab, setTab] = useState<VisTab>(() => {
    if (oauthReturn) return "settings";
    return initialTab && (TAB_KEYS as string[]).includes(initialTab) ? (initialTab as VisTab) : "google";
  });

  // One toast for the OAuth round trip, then strip the params so a reload
  // does not repeat it. replaceState keeps the Next router state intact.
  useEffect(() => {
    if (!oauthReturn) return;
    if (oauthReturn.status === "connected") toast.success("Google connected", "Pick this hotel's location to start the daily sync.");
    else if (oauthReturn.status === "denied") toast.info("Google not connected", "The permission request was declined.");
    else toast.error("Google connection failed", oauthReturn.error ? `Reason: ${oauthReturn.error}` : undefined);
    try {
      const url = new URL(window.location.href);
      ["oauth", "oauth_status", "oauth_error"].forEach(k => url.searchParams.delete(k));
      url.searchParams.set("tab", "settings");
      window.history.replaceState(window.history.state, "", url.toString());
    } catch { /* cosmetic only */ }
    // Once per mount: the params are consumed on first render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pick = (k: VisTab) => {
    setTab(k);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", k);
      window.history.replaceState(window.history.state, "", url.toString());
    } catch { /* cosmetic only */ }
  };

  const tabs: TabItem<VisTab>[] = [
    { key: "google", label: "Google" },
    { key: "ai", label: "AI answers" },
    { key: "rank", label: "Rank" },
    { key: "site", label: "Site health" },
    { key: "costs", label: "Costs" },
    { key: "settings", label: "Settings" },
  ];

  return (
    <div className="vis-stack">
      <style dangerouslySetInnerHTML={{ __html: visStyles }} />
      <PageHeader eyebrow="Visibility" title="Google and AI visibility"
        subtitle="Your official price on Google, OTA undercuts, map rank, and what AI answer engines say about you." />
      {!canView ? (
        <Card><EmptyState title="No access" body="Ask the owner or GM for visibility access." /></Card>
      ) : (
        <>
          <Tabs tabs={tabs} value={tab} onChange={pick} ariaLabel="Visibility sections" />
          {tab === "google" && <GoogleTab onOpenSettings={() => pick("settings")} />}
          {tab === "ai" && <AiTab onOpenSettings={() => pick("settings")} />}
          {tab === "rank" && <RankTab onOpenSettings={() => pick("settings")} />}
          {tab === "site" && <SiteHealthTab onOpenSettings={() => pick("settings")} />}
          {tab === "costs" && <CostsTab />}
          {tab === "settings" && <SettingsTab />}
        </>
      )}
    </div>
  );
}
