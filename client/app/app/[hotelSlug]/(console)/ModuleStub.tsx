"use client";

/* Placeholder for a module whose lane has not landed yet, so every nav link
   resolves from the foundation wave on. Each module lane replaces its own
   (console)/<module>/page.tsx; this file stays for any still unbuilt. */

import { Card, EmptyState, PageHeader } from "../../../ui";
import { navItem, type NavKey } from "./nav";

export default function ModuleStub({ module }: { module: NavKey }) {
  const item = navItem(module);
  const Icon = item.icon;
  return (
    <>
      <PageHeader eyebrow="Clefs" title={item.label} subtitle={item.blurb} />
      <Card>
        <EmptyState
          icon={<Icon />}
          title="Coming in the next build wave"
          body={`${item.label} is being built now. This page will fill in once it ships; nothing needs setting up here yet.`}
        />
      </Card>
    </>
  );
}
