"use client";

/* Confirm (or correct) an extracted contract (README "Contracts:
   extraction then confirmation"). The AI reading is a DRAFT the checks
   ignore until a person confirms it; the terms that change what Clefs
   claims are editable here, and everything else (clauses, dispute and card
   rules) is sent back as read. Confirming closes the open-ended contract it
   renews; any other overlap is refused (409 contract_overlap). */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { pgApi } from "../lib";
import type { Contract } from "../types";
import { errorText } from "./bits";
import { BASE_RULE_LABEL } from "./ContractCard";

const CHARGE_TYPES = ["MUNICIPALITYFEE", "TOURISMFEE", "SERVICECHARGE", "VAT", "DESTINATIONFEE"] as const;

export function ContractEditor({
  slug, contract, onClose, onSaved,
}: {
  slug: string;
  contract: Contract;
  onClose: () => void;
  onSaved: (c: Contract) => void;
}) {
  const toast = useToast();
  const [entity, setEntity] = useState(contract.entity_name ?? "");
  const [country, setCountry] = useState(contract.entity_country ?? "");
  const [from, setFrom] = useState(contract.effective_from ?? "");
  const [to, setTo] = useState(contract.effective_to ?? "");
  const [pct, setPct] = useState(contract.base_commission_pct ?? "");
  const [rule, setRule] = useState<Contract["commission_base_rule"]>(contract.commission_base_rule);
  const [excluded, setExcluded] = useState<string[]>(contract.excluded_charge_types ?? []);
  const [note, setNote] = useState(contract.note ?? "");
  const [busy, setBusy] = useState(false);

  const pctOk = /^\d{1,2}(\.\d{1,3})?$/.test(pct.trim()) || pct.trim() === "100";
  const countryOk = !country.trim() || /^[A-Za-z]{2}$/.test(country.trim());
  const datesOk = Boolean(from) && (!to || to >= from);
  const ok = pctOk && countryOk && datesOk;

  const toggle = (t: string) => setExcluded(x => (x.includes(t) ? x.filter(v => v !== t) : [...x, t]));

  async function save() {
    if (!ok || contract.id === null) return;
    setBusy(true);
    try {
      const out = await api.put<Contract>(pgApi(slug, `/contracts/${contract.id}`), {
        ota_code: contract.ota_code, entity_name: entity.trim() || null, entity_country: country.trim().toUpperCase() || null,
        effective_from: from, effective_to: to || null, base_commission_pct: pct.trim(), commission_base_rule: rule,
        excluded_charge_types: excluded, collect_models: contract.collect_models ?? [], dispute_rules: contract.dispute_rules ?? [],
        vcc_rules: contract.vcc_rules ?? {}, clauses: contract.clauses ?? [], note: note.trim() || null, confirm: true,
      });
      toast({ tone: "ok", title: "Contract confirmed", body: "The checks use these terms from now on." });
      onSaved(out);
    } catch (err) {
      toast({ tone: "bad", title: "Could not confirm the contract", body: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} dismissible={!busy} size="lg" title="Confirm the contract terms"
           footer={<>
             <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
             <Button variant="primary" loading={busy} disabled={!ok} onClick={save}>Confirm terms</Button>
           </>}>
      <div className="cx-stack">
        <p className="pg-muted" style={{ margin: 0 }}>
          Compare each term with the clause cited under the contract. Only confirmed terms are used to judge commission.
        </p>
        <div className="pg-toolbar">
          <Field label="Commission %" error={pctOk ? undefined : "A percent like 15 or 17.5"}>
            <Input inputMode="decimal" value={pct} onChange={e => setPct(e.target.value)} />
          </Field>
          <Field label="Effective from" error={datesOk ? undefined : "Needed, and before the end date"}>
            <Input type="date" value={from} onChange={e => setFrom(e.target.value)} />
          </Field>
          <Field label="Effective to" hint="Empty for open-ended.">
            <Input type="date" value={to} onChange={e => setTo(e.target.value)} />
          </Field>
        </div>
        <Field label="Commission is charged on">
          <Select value={rule} onChange={e => setRule(e.target.value as Contract["commission_base_rule"])}>
            {Object.entries(BASE_RULE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Charges excluded from the commission base">
          <div className="pg-row">
            {CHARGE_TYPES.map(t => (
              <label key={t} className="pg-row" style={{ gap: 6, fontSize: "0.86rem" }}>
                <input type="checkbox" checked={excluded.includes(t)} onChange={() => toggle(t)} /> {t.toLowerCase().replace("fee", " fee")}
              </label>
            ))}
          </div>
        </Field>
        <div className="pg-toolbar">
          <Field label="Invoicing entity"><Input value={entity} maxLength={200} onChange={e => setEntity(e.target.value)} /></Field>
          <Field label="Entity country" hint="Two letters, e.g. NL." error={countryOk ? undefined : "Two letters"}>
            <Input value={country} maxLength={2} onChange={e => setCountry(e.target.value)} />
          </Field>
        </div>
        <Field label="Note"><Textarea rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}
