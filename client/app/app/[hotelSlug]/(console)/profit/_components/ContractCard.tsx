"use client";

/* One OTA contract as the checks read it: the commission percent and base
   rule, the charges excluded from the base, the dispute windows and card
   rules, and every clause the extraction cited (reference, page and the
   verbatim text), each linked to its page in the source PDF. Review flags
   say what the reader could not cite or had to fill from OTA defaults. */

import type { ReactNode } from "react";
import { LuExternalLink } from "react-icons/lu";
import { API_PREFIX, hotelPath } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Card, type Tone } from "../../../../../ui";
import { disputeRuleText, humanKey, otaLabel, vccRuleText } from "../lib";
import type { Clause, Contract } from "../types";

export const BASE_RULE_LABEL: Record<string, string> = {
  ROOM_INCL_VAT: "Room rate incl. VAT and service charge (fees excluded when set up as charges)",
  RSR_TAX_INCL: "Sell rate incl. taxes (Agoda RSR)",
  NET_RATE: "Net rate",
  ROOM_EXCL_TAX: "Room rate excl. taxes",
};

const STATUS_TONE: Record<string, Tone> = { DRAFT: "gold", CONFIRMED: "ok", RETIRED: "neutral", DEFAULT: "info" };
const STATUS_TEXT: Record<string, string> = {
  DRAFT: "Needs review", CONFIRMED: "Confirmed", RETIRED: "Retired", DEFAULT: "OTA default terms",
};

/** "commission_pct_missing" -> readable; unknown flags pass through humanised. */
export function flagText(flag: string): string {
  if (flag.startsWith("uncited:")) return `No clause cited for ${humanKey(flag.slice(8))}`;
  if (flag.startsWith("citation_page_out_of_range:")) return `Citation ${flag.split(":")[1]} pointed past the last page and was dropped`;
  if (flag.endsWith("_missing")) return `${humanKey(flag.slice(0, -8))} not found; the OTA default was filled in`;
  if (flag === "effective_to_before_from") return "The end date is before the start date";
  if (flag === "low_confidence") return "The reader was unsure; check every term";
  return humanKey(flag);
}


export function ClauseList({ clauses, fileId, slug }: { clauses: readonly Clause[]; fileId: string | null; slug: string }) {
  if (!clauses.length) return <div className="pg-faint">No clauses cited.</div>;
  return (
    <div className="cx-stack">
      {clauses.map((c, i) => (
        <blockquote key={`${c.ref}-${i}`} className="pg-clause">
          {c.text}
          <cite>
            {c.ref}{c.page ? `, page ${c.page}` : ""}{c.field ? ` · supports ${humanKey(c.field)}` : ""}
            {fileId && c.page ? (
              <> · <a className="cx-link" target="_blank" rel="noopener noreferrer"
                      href={`${API_PREFIX}${hotelPath(slug, `/files/${encodeURIComponent(fileId)}`)}#page=${c.page}`}>
                Open page <LuExternalLink aria-hidden="true" size={11} />
              </a></>
            ) : null}
          </cite>
        </blockquote>
      ))}
    </div>
  );
}

export function ContractCard({ c, slug, actions }: { c: Contract; slug: string; actions?: ReactNode }) {
  const fmt = useFormat();
  const span = c.is_default ? "Used until a contract is confirmed"
    : `${c.effective_from ? fmt.date(c.effective_from, "medium") : "?"} to ${c.effective_to ? fmt.date(c.effective_to, "medium") : "open-ended"}`;
  return (
    <Card title={`${otaLabel(c.ota_code)}${c.entity_name ? `: ${c.entity_name}` : ""}`} subtitle={span}
          gold={c.status === "DRAFT"}
          actions={<>{actions}<Badge tone={STATUS_TONE[c.status] ?? "neutral"} dot>{STATUS_TEXT[c.status] ?? c.status}</Badge></>}>
      <div className="cx-stack">
        {(c.review_flags ?? []).length > 0 && (
          <div className="pg-notice">
            <div>
              <strong>Check before confirming</strong>
              <ul>{(c.review_flags ?? []).map(f => <li key={f}>{flagText(f)}</li>)}</ul>
            </div>
          </div>
        )}
        <dl className="pg-kv">
          <dt>Commission</dt><dd className="pg-strong">{c.base_commission_pct}%</dd>
          <dt>Charged on</dt><dd>{BASE_RULE_LABEL[c.commission_base_rule] ?? c.commission_base_rule}</dd>
          <dt>Excluded from base</dt><dd>{c.excluded_charge_types.length ? c.excluded_charge_types.map(humanKey).join(", ") : "Nothing"}</dd>
          {c.entity_country && (<><dt>Invoicing entity</dt><dd>{c.entity_name ?? "n/a"} ({c.entity_country})</dd></>)}
          {c.collect_models.length > 0 && (<><dt>Collect models</dt><dd>{c.collect_models.map(humanKey).join(", ")}</dd></>)}
          <dt>Dispute windows</dt><dd>{(c.dispute_rules ?? []).length ? c.dispute_rules.map(disputeRuleText).join("; ") : "None"}</dd>
          <dt>Virtual cards</dt><dd>{vccRuleText(c.vcc_rules)}</dd>
          {c.extracted_by && (<><dt>Read by</dt><dd>{c.extracted_by === "LLM" ? "AI, with citations" : "Entered by hand"}</dd></>)}
        </dl>
        {!c.is_default && <ClauseList clauses={c.clauses ?? []} fileId={c.source_file_id} slug={slug} />}
      </div>
    </Card>
  );
}
