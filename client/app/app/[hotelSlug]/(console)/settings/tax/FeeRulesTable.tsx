"use client";

/* FeeRulesTable: every fee rule that can reach this hotel, read-only.
   Rules are law, not settings: they carry the source they were read from
   and who verified them, and the owner changes nothing here. What the owner
   controls (category, convention, the 2026 announcements) lives in the
   cards above. Showing verified_by next to each rate is the trust device:
   a GM can click through to the Dubai Economy and Tourism circular. */

import { useState } from "react";
import { useFormat } from "../../../../../lib/format";
import { Badge, Table, type Column, type Tone } from "../../../../../ui";
import { categoryLabel, FEE_LABEL } from "./api";
import { active, type FeeRule } from "./convention";

const STATUS: Record<string, { label: string; tone: Tone }> = {
  VERIFIED: { label: "Verified", tone: "ok" },
  ANNOUNCED_UNVERIFIED: { label: "Announced, unverified", tone: "warn" },
  OWNER_CONFIGURED: { label: "Set by you", tone: "info" },
};

const SENTINEL = "2000-01-01"; // "in force before any data Clefs imports" (core/tax README)

function pct(rate: string): string {
  const n = Number(rate) * 100;
  return `${Math.round(n * 10000) / 10000}%`;
}

export interface FeeRulesTableProps {
  rules: readonly FeeRule[];
  category: string | null;
  /** The date "applies" is judged on (YYYY-MM-DD). */
  on: string;
  currency: string;
}

export function FeeRulesTable({ rules, category, on, currency }: FeeRulesTableProps) {
  const fmt = useFormat();
  const [mineOnly, setMineOnly] = useState(true);
  const list = mineOnly && category
    ? rules.filter(r => r.property_category === "*" || r.property_category === category)
    : rules;

  const rate = (r: FeeRule) => {
    if (r.calc_mode === "PERCENT") return <span className="tax-rate">{pct(r.rate)}</span>;
    if (r.calc_mode === "PER_ROOM_NIGHT") {
      return (
        <div>
          <span className="tax-rate">{fmt.money(r.rate, { currency: r.currency || currency })}</span>
          <div className="tax-sub">
            per {r.per_bedroom ? "bedroom" : "room"} night{r.cap_consecutive_nights ? `, first ${r.cap_consecutive_nights} nights` : ""}
          </div>
        </div>
      );
    }
    const months = r.params?.remit_deferral_months;
    return <span className="tax-rate">{typeof months === "number" ? `Pay ${months} months later` : r.calc_mode}</span>;
  };

  const columns: Column<FeeRule>[] = [
    {
      key: "fee", header: "Fee",
      render: r => (
        <div>
          <div style={{ fontWeight: 600 }}>{FEE_LABEL[r.fee_code] ?? r.fee_code}</div>
          <div className="tax-sub">{r.label || r.rule_key}</div>
        </div>
      ),
    },
    { key: "cat", header: "Applies to", hideOnMobile: true, render: r => categoryLabel(r.property_category) },
    { key: "rate", header: "Rate", render: rate },
    {
      key: "base", header: "Charged on", hideOnMobile: true,
      render: r => (r.calc_mode === "PERCENT" ? (r.base?.length ? r.base : ["ROOM"]).join(" + ") : "-"),
    },
    {
      key: "window", header: "In force",
      render: r => (
        <span className="tax-sub">
          {r.effective_from <= SENTINEL ? "Long-standing" : `From ${fmt.date(r.effective_from, "medium")}`}
          {r.effective_to ? ` to ${fmt.date(r.effective_to, "medium")}` : ""}
        </span>
      ),
    },
    {
      key: "status", header: "Status",
      render: r => {
        const s = STATUS[r.rule_status] ?? { label: r.rule_status, tone: "neutral" as Tone };
        const applies = r.applies_on ?? active(r, on, category);
        return (
          <div className="cx-stack" style={{ gap: 4 }}>
            <Badge tone={s.tone}>{s.label}</Badge>
            {applies && <Badge tone="gold" dot>Applies {on === fmt.dayKey(new Date()) ? "today" : "on the date"}</Badge>}
            {r.override && <span className="tax-sub">{r.override.apply ? "You applied it" : "You switched it off"}</span>}
          </div>
        );
      },
    },
    {
      key: "verified", header: "Verified by", hideOnMobile: true,
      render: r => (
        <div>
          <div>{r.verified_by || (r.rule_status === "ANNOUNCED_UNVERIFIED" ? "Not yet" : "-")}</div>
          {r.verified_at && <div className="tax-sub">{fmt.date(r.verified_at, "medium")}</div>}
        </div>
      ),
    },
    {
      key: "src", header: "Source",
      render: r => (r.source_url
        ? <a className="tax-src" href={r.source_url} target="_blank" rel="noopener noreferrer">Open source</a>
        : <span className="tax-sub">{r.hotel_slug ? "Your setting" : "-"}</span>),
    },
  ];

  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      {category && (
        <label className="tax-switch">
          <input type="checkbox" checked={mineOnly} onChange={e => setMineOnly(e.target.checked)} />
          Only rules for {categoryLabel(category).toLowerCase()}
        </label>
      )}
      <Table columns={columns} rows={list} rowKey={r => r.id} caption="Fee rules"
             empty={<p className="tax-note">No fee rules for this hotel. Rules are seeded for Dubai and Abu Dhabi; other emirates need the owner to configure them.</p>} />
    </div>
  );
}
