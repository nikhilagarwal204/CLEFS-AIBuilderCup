"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Service setup (03 spec 1.7 config routes): departments, request types,
   response times (SLA and escalation), recovery authority, rooms.
   Amenity items live in the Amenities module (Canon 8) and are linked, not
   duplicated. service.manage only; the server enforces it too.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useHotel } from "../../../../../lib/hotel";
import { useFormat } from "../../../../../lib/format";
import { Button, Card, Tabs, type TabItem } from "../../../../../ui";
import ServiceShell from "../_components/ServiceShell";
import { SEED_DEPARTMENTS, SEED_REQUEST_TYPES, type Department, type RequestType } from "../_components/catalog";
import type { CompensationRule, SlaPolicy } from "../_components/configApi";
import { useSection } from "./useSection";
import { DepartmentsSection, RequestTypesSection } from "./Sections";
import { CompensationSection, SlaSection } from "./PolicySections";
import RoomsImport from "./RoomsImport";
import { SEED_SLA, seedCompensation } from "./seeds";

type Tab = "departments" | "types" | "sla" | "recovery" | "rooms" | "amenities";
const TAB_KEYS: Tab[] = ["departments", "types", "sla", "recovery", "rooms", "amenities"];

export default function ConfigConsole({ initialTab }: { initialTab?: string }) {
  const { slug } = useHotel();
  const hf = useFormat();
  const [tab, setTab] = useState<Tab>(initialTab && (TAB_KEYS as string[]).includes(initialTab) ? (initialTab as Tab) : "departments");

  const departments = useSection<Department>(slug, "departments", SEED_DEPARTMENTS);
  const types = useSection<RequestType>(slug, "request-types", SEED_REQUEST_TYPES);
  const sla = useSection<SlaPolicy>(slug, "sla-policies", SEED_SLA);
  const recovery = useSection<CompensationRule>(slug, "compensation", seedCompensation(hf.currency));

  const tabs: TabItem<Tab>[] = [
    { key: "departments", label: "Departments", count: departments.rows.length },
    { key: "types", label: "Request types", count: types.rows.length },
    // "Response times" is the owner view; these are the targets it is measured against.
    { key: "sla", label: "Response targets" },
    { key: "recovery", label: "Recovery authority" },
    { key: "rooms", label: "Rooms" },
    { key: "amenities", label: "Amenities" },
  ];
  const dirty = departments.dirty || types.dirty || sla.dirty || recovery.dirty;

  return (
    <ServiceShell tab="config" perm="service.manage" title="Service setup"
                  subtitle="Who handles what, how fast, and what the team may give to put things right."
                  actions={dirty ? <span className="svc-chip svc-chip--warn">Unsaved changes</span> : undefined}>
      <div className="svc-stack">
        <Tabs tabs={tabs} value={tab} onChange={setTab} ariaLabel="Setup sections" />
        {tab === "departments" && <DepartmentsSection state={departments} />}
        {tab === "types" && <RequestTypesSection state={types} departments={departments.rows} />}
        {tab === "sla" && <SlaSection state={sla} />}
        {tab === "recovery" && <CompensationSection state={recovery} />}
        {tab === "rooms" && <RoomsImport slug={slug} />}
        {tab === "amenities" && (
          <Card title="Amenities" subtitle="Towels, pillows, toiletries and their stock live in the Amenities module, so the phone console and the guest QR offer the same list.">
            <Button href={`/app/${encodeURIComponent(slug)}/amenities`}>Open Amenities</Button>
          </Card>
        )}
      </div>
    </ServiceShell>
  );
}
