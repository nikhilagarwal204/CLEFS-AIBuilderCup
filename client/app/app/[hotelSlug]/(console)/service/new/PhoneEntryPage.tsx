"use client";

import { Card } from "../../../../../ui";
import PhoneEntryConsole from "../_components/PhoneEntryConsole";
import ServiceShell from "../_components/ServiceShell";

/* The page form of the phone-entry console: same component as the "N"
   overlay on the live queue, with room to breathe. */
export default function PhoneEntryPage() {
  return (
    <ServiceShell tab="new" perm={["service.work", "service.manage"]} title="Phone entry"
                  subtitle="A guest called the desk. Room, department, what they need, Enter.">
      <Card gold>
        <PhoneEntryConsole />
      </Card>
    </ServiceShell>
  );
}
