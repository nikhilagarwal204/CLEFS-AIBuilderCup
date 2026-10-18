"use client";

/* ═══════════════════════════════════════════════════════════════════════
   OTA contracts (02 spec 3.10 contracts/): upload a contract PDF, the AI
   reads it into a DRAFT with a cited clause for every term, a person
   checks each term against its citation and confirms it. Until then the
   checks use the OTA's default terms, shown at the bottom so the owner
   sees exactly what Clefs assumes today.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Field, FileDrop, ProgressBar, Select, useToast, useUpload } from "../../../../../ui";
import { OTA_LABEL, pgApi } from "../lib";
import type { Contract, ContractsResponse } from "../types";
import { DataCard, errorText, useCanManage } from "./bits";
import { ContractCard } from "./ContractCard";
import { ContractEditor } from "./ContractEditor";

function Extractor({ onDraft }: { onDraft: (c: Contract) => void }) {
  const { slug } = useHotel();
  const toast = useToast();
  const upload = useUpload(slug, "profit.contract");
  const [ota, setOta] = useState("BOOKING");
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFiles(files: File[]) {
    const file = files[0];
    if (!file) return;
    setError(null);
    const ref = await upload.upload(file);
    if (!ref) return;
    setReading(true);
    try {
      const c = await api.post<Contract>(pgApi(slug, "/contracts/extract"), { file_id: ref.id, ota_code: ota });
      toast({ tone: "ok", title: "Contract read", body: "Check each term against its clause, then confirm." });
      onDraft(c);
      upload.reset();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setReading(false);
    }
  }

  const busy = upload.uploading || reading;
  return (
    <div className="cx-stack">
      <Field label="OTA">
        <Select value={ota} disabled={busy} onChange={e => setOta(e.target.value)}>
          {["BOOKING", "AGODA", "EXPEDIA"].map(o => <option key={o} value={o}>{OTA_LABEL[o]}</option>)}
        </Select>
      </Field>
      <FileDrop accept=".pdf,.txt,application/pdf" disabled={busy} onFiles={onFiles} onReject={r => setError(r)}
                title={reading ? "Reading the contract..." : busy ? "Uploading..." : "Drop the contract PDF here or tap to choose"}
                hint="The PDF with a text layer, up to 25 MB. Scanned pages without text cannot be read.">
        {upload.uploading && <div style={{ width: "100%", marginTop: 10 }}><ProgressBar value={upload.progress} /></div>}
      </FileDrop>
      {(error || upload.error) && <div className="pg-error" role="alert">{error ?? upload.error?.message}</div>}
    </div>
  );
}

export default function ContractsView() {
  const { slug } = useHotel();
  const toast = useToast();
  const canManage = useCanManage();
  const q = useApi<ContractsResponse>(pgApi(slug, "/contracts"));
  const [editing, setEditing] = useState<Contract | null>(null);
  const [retiring, setRetiring] = useState<number | null>(null);

  const contracts = q.data?.contracts ?? [];
  const drafts = contracts.filter(c => c.status === "DRAFT");
  const live = contracts.filter(c => c.status === "CONFIRMED");
  const retired = contracts.filter(c => c.status === "RETIRED");
  const covered = new Set(live.map(c => c.ota_code));
  const defaults = (q.data?.defaults ?? []).filter(d => !covered.has(d.ota_code));

  async function retire(c: Contract) {
    if (c.id === null) return;
    setRetiring(c.id);
    try {
      await api.del(pgApi(slug, `/contracts/${c.id}`));
      toast({ tone: "ok", title: c.status === "DRAFT" ? "Draft discarded" : "Contract retired" });
      q.reload();
    } catch (err) {
      toast({ tone: "bad", title: "Could not retire the contract", body: errorText(err) });
    } finally {
      setRetiring(null);
    }
  }

  const actionsFor = (c: Contract) => (canManage && c.id !== null ? (
    <>
      {c.status !== "RETIRED" && (
        <Button size="sm" variant={c.status === "DRAFT" ? "primary" : "secondary"} onClick={() => setEditing(c)}>
          {c.status === "DRAFT" ? "Review and confirm" : "Edit"}
        </Button>
      )}
      {c.status !== "RETIRED" && (
        <Button size="sm" variant="ghost" loading={retiring === c.id} onClick={() => retire(c)}>
          {c.status === "DRAFT" ? "Discard" : "Retire"}
        </Button>
      )}
    </>
  ) : undefined);

  return (
    <>
      {canManage && (
        <Card title="Read a contract" subtitle="Every term is read with a cited clause and waits for your confirmation.">
          <Extractor onDraft={c => { q.reload(); setEditing(c); }} />
        </Card>
      )}
      <DataCard title="Contracts" loading={q.loading} error={q.error} lines={4}
                empty={!contracts.length} emptyTitle="No contracts on file"
                emptyBody="The checks use each OTA's standard terms until you add yours.">
        <div className="cx-stack">
          {[...drafts, ...live, ...retired].map(c => <ContractCard key={c.id ?? c.ota_code} c={c} slug={slug} actions={actionsFor(c)} />)}
        </div>
      </DataCard>
      {defaults.length > 0 && (
        <Card title="Standard terms in use" subtitle="For OTAs without a confirmed contract. Upload yours if they differ.">
          <div className="cx-stack">
            {defaults.map(d => <ContractCard key={`default-${d.ota_code}`} c={d} slug={slug} />)}
          </div>
        </Card>
      )}
      {editing && (
        <ContractEditor slug={slug} contract={editing} onClose={() => setEditing(null)}
                        onSaved={() => { setEditing(null); q.reload(); }} />
      )}
    </>
  );
}
