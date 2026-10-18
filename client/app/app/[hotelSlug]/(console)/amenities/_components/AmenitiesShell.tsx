"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Amenities console chrome (02 spec 5.3): the page header, the route tabs
   (overview, issue, catalog, par, benchmarks) and the one style string.
   Each route is its own page so a housekeeping phone can bookmark
   /amenities/issue and land on the stepper grid directly.

   Tabs are filtered with can(): a floor attendant holds amenities.issue
   only, so they see Issue (and the read-only catalog) and never a leakage
   figure. The server stays the authority; a 403 still surfaces as a
   message, never a blank page.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { LuGift, LuLock } from "react-icons/lu";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Card, EmptyState, PageHeader, Skeleton, Tabs, type TabItem } from "../../../../../ui";
import { navHref, navItem } from "../../nav";
import { amnStyles } from "./styles";

// ─── Permissions ─────────────────────────────────────────────────────────

export const PERM_VIEW = "amenities.view";
export const PERM_MANAGE = "amenities.manage";
export const PERM_ISSUE = "amenities.issue";

export type AmnTab = "overview" | "issue" | "catalog" | "par" | "benchmarks" | "item";

/** Which permissions open each tab (any one is enough). */
const TAB_PERMS: Record<Exclude<AmnTab, "item">, readonly string[]> = {
  overview: [PERM_VIEW],
  issue: [PERM_ISSUE, PERM_MANAGE],
  catalog: [PERM_VIEW, PERM_ISSUE, PERM_MANAGE],
  par: [PERM_VIEW, PERM_ISSUE, PERM_MANAGE],
  benchmarks: [PERM_VIEW],
};

const TAB_LABELS: Record<Exclude<AmnTab, "item">, string> = {
  overview: "Overview", issue: "Issue", catalog: "Catalog", par: "Par levels", benchmarks: "Benchmarks",
};

const TAB_SUB: Record<Exclude<AmnTab, "item">, string> = {
  overview: "", issue: "/issue", catalog: "/catalog", par: "/par", benchmarks: "/benchmarks",
};

/** Link to an amenities route for this hotel. */
export function amnHref(slug: string, sub = ""): string {
  return navHref(slug, navItem("amenities"), sub);
}

// ─── Shell ───────────────────────────────────────────────────────────────

export default function AmenitiesShell({
  active, title, subtitle, actions, need, children,
}: {
  active: AmnTab;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Permissions that open this page (any one). */
  need: readonly string[];
  children: ReactNode;
}) {
  const { slug } = useHotel();
  const { can, loading } = useSession();

  const tabs: TabItem<Exclude<AmnTab, "item">>[] = (Object.keys(TAB_LABELS) as Exclude<AmnTab, "item">[])
    .filter(k => can(TAB_PERMS[k]))
    .map(k => ({ key: k, label: TAB_LABELS[k], href: amnHref(slug, TAB_SUB[k]) }));
  const tabValue = active === "item" ? "overview" : active;
  const allowed = can(need);

  return (
    <>
      {/* Injected raw: SSR would escape the ">" child selectors as text. */}
      <style dangerouslySetInnerHTML={{ __html: amnStyles }} />
      <PageHeader
        eyebrow="Amenities"
        title={title ?? "Amenity leakage"}
        subtitle={subtitle ?? "What was handed out against what your standards and logged requests say should have been."}
        actions={allowed ? actions : undefined}
      />
      {tabs.length > 1 && (
        <div className="amn-nav">
          <Tabs tabs={tabs} value={tabValue} ariaLabel="Amenities sections" />
        </div>
      )}
      {loading ? (
        <Card><Skeleton lines={4} /></Card>
      ) : allowed ? (
        children
      ) : (
        <Card>
          <EmptyState
            icon={<LuLock />}
            title="This page is not part of your role"
            body="Ask the owner or general manager for amenities access if you need it."
          />
        </Card>
      )}
    </>
  );
}

/** Standard empty card for an amenities list with nothing yet. */
export function AmnEmpty({ title, body, action }: { title: string; body?: ReactNode; action?: ReactNode }) {
  return <EmptyState icon={<LuGift />} title={title} body={body} action={action} />;
}
