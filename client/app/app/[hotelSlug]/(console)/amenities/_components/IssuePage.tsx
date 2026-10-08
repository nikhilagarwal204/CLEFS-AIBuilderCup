"use client";

/* /amenities/issue: quick entry on top, what was logged today below it,
   and (for managers) the file import. Floor staff holding only
   amenities.issue see the stepper grid and today's lines, nothing else. */

import { useState } from "react";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Segmented } from "../../../../../ui";
import AmenitiesShell, { PERM_ISSUE, PERM_MANAGE } from "./AmenitiesShell";
import ImportPanel from "./ImportPanel";
import IssuanceLog from "./IssuanceLog";
import IssuanceQuickEntry from "./IssuanceQuickEntry";

type Mode = "quick" | "import";

export default function IssuePage() {
  const { can } = useSession();
  const fmt = useFormat();
  const canImport = can(PERM_MANAGE);
  const [mode, setMode] = useState<Mode>("quick");
  const [version, setVersion] = useState(0);
  const today = fmt.dayKey(new Date());
  const bump = () => setVersion(v => v + 1);

  return (
    <AmenitiesShell
      active="issue" need={[PERM_ISSUE, PERM_MANAGE]}
      title="Log issuance"
      subtitle="Tap + for each item going out, then log the lot. Returns and damage have their own reason."
    >
      {canImport && (
        <div className="amn-toolbar">
          <Segmented ariaLabel="Entry mode" value={mode} onChange={setMode}
                     options={[{ value: "quick", label: "Quick entry" }, { value: "import", label: "Import a file" }]} />
        </div>
      )}
      {mode === "import" && canImport ? (
        <div className="amn-stack">
          <ImportPanel onCommitted={bump} />
          <IssuanceLog from={today} to={today} version={version} title="Logged today" />
        </div>
      ) : (
        <div className="amn-stack">
          <IssuanceQuickEntry onLogged={bump} />
          <IssuanceLog from={today} to={today} version={version} title="Logged today" limit={50} />
        </div>
      )}
    </AmenitiesShell>
  );
}
