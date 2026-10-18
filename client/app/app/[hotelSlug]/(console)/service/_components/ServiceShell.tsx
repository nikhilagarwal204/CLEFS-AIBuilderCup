"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Frame for the new Service pages (/service/new, config, roster,
   analytics): the page gutter (Service is a full-bleed nav item), the
   Service sub-nav as route tabs, and the svc- CSS. The live queue itself
   stays the classic dashboard at /service until it is extracted.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { EmptyState, PageHeader, Tabs, type TabItem } from "../../../../../ui";
import { SvcStyles } from "./RequestMeta";

export type ServiceTab = "queue" | "new" | "config" | "roster" | "analytics";

export default function ServiceShell({
  tab, title, subtitle, actions, perm, children,
}: {
  tab: ServiceTab;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Permission(s) this page needs; any one is enough. */
  perm: string | readonly string[];
  children: ReactNode;
}) {
  const { slug } = useHotel();
  const { can, loading } = useSession();
  const base = `/app/${encodeURIComponent(slug)}/service`;
  // Route tabs cannot be disabled (they are links), so hide what the role lacks.
  const tabs: TabItem<ServiceTab>[] = ([
    { key: "queue", label: "Live queue", href: base, show: true },
    { key: "new", label: "Phone entry", href: `${base}/new`, show: can(["service.work", "service.manage"]) },
    { key: "roster", label: "Roster", href: `${base}/roster`, show: can("service.view") },
    { key: "analytics", label: "Analytics", href: `${base}/analytics`, show: can("service.view") },
    { key: "config", label: "Setup", href: `${base}/config`, show: can("service.manage") },
  ] as (TabItem<ServiceTab> & { show: boolean })[])
    .filter(t => t.show || t.key === tab)
    .map(({ show: _show, ...t }) => t);
  const allowed = can(perm);
  return (
    <div className="svc-page cx-root">
      <SvcStyles />
      <div className="svc-subnav"><Tabs tabs={tabs} value={tab} ariaLabel="Service" /></div>
      <PageHeader eyebrow="Service Control" title={title} subtitle={subtitle} actions={allowed ? actions : undefined} />
      {!loading && !allowed
        ? <EmptyState title="Not available for your role" body="Ask a manager if you need this page." />
        : children}
    </div>
  );
}
