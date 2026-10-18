"use client";

/* Inquiry settings shell: one tab per concern. Each tab loads its own data
   so a failing provider call never blanks the others. Editing needs
   inquiries.manage (owner, GM); viewers see rules and templates read-only. */

import { useState } from "react";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, PageHeader, Tabs, type TabItem } from "../../../../../ui";
import { inqStyles } from "../styles";
import ChannelsTab from "./ChannelsTab";
import PaymentsTab from "./PaymentsTab";
import RulesTab from "./RulesTab";
import TemplatesTab from "./TemplatesTab";
import WidgetTab from "./WidgetTab";

type TabKey = "channels" | "widget" | "payments" | "rules" | "templates";
const KEYS: TabKey[] = ["channels", "widget", "payments", "rules", "templates"];

export default function InquirySettings({ initialTab }: { initialTab?: string }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const canManage = can("inquiries.manage");
  const canView = can("inquiries.view");
  const [tab, setTab] = useState<TabKey>(() =>
    initialTab && (KEYS as string[]).includes(initialTab) ? (initialTab as TabKey) : canManage ? "channels" : "rules");

  if (!canView) {
    return <Card><EmptyState title="No access" body="Your role cannot see inquiry settings." /></Card>;
  }

  const tabs: TabItem<TabKey>[] = [
    { key: "channels", label: "Channels", disabled: !canManage },
    { key: "widget", label: "Website form", disabled: !canManage },
    { key: "payments", label: "Payments", disabled: !canManage },
    { key: "rules", label: "Reply and quote rules" },
    { key: "templates", label: "WhatsApp templates" },
  ];

  return (
    <div>
      <style dangerouslySetInnerHTML={{ __html: inqStyles }} />
      <PageHeader eyebrow="Inquiries" title="Inquiry settings"
        subtitle="Where inquiries come from, how fast they must be answered, how quotes are priced and how guests pay."
        actions={<Button href={`/app/${slug}/inquiries`}>Back to inquiries</Button>} />
      <Tabs tabs={tabs} value={tab} onChange={setTab} ariaLabel="Inquiry settings" />
      <div className="inq-tabs-body">
        {tab === "channels" && canManage && <ChannelsTab />}
        {tab === "widget" && canManage && <WidgetTab />}
        {tab === "payments" && canManage && <PaymentsTab />}
        {tab === "rules" && <RulesTab canManage={canManage} />}
        {tab === "templates" && <TemplatesTab canManage={canManage} />}
      </div>
    </div>
  );
}
