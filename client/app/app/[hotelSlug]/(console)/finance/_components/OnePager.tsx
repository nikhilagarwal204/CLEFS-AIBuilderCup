"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The monthly one-pager (04 spec 6.8): verified profit first, then GOP,
   margin, GOPPAR with the market chip and its caveat, TRevPAR, RevPAR,
   flow-through and labour %. Every figure is read from the published
   month (confirmed mapping only), with deltas against the month before.
   ═══════════════════════════════════════════════════════════════════════ */

import { LuExternalLink } from "react-icons/lu";
import { Badge, StatTile } from "../../../../../ui";
import { changeDelta, monthLabel, NA, pctText, ppDelta, ratioText } from "../lib";
import type { MarketIndex, Monthly } from "../types";
import { Money } from "./bits";

export default function OnePager({ m, prev }: { m: Monthly; prev?: Monthly | null }) {
  const ccy = m.currency;
  const vs = prev ? `vs ${monthLabel(prev.month, true)}` : undefined;
  const ftBasis = m.flow_through_basis === "yoy" ? "against the same month last year"
    : m.flow_through_basis === "mom" ? "against the month before" : "n/a when revenue moved under 1%";
  return (
    <>
      {m.booked_net && (
        <div className="fin-notice" role="note">
          OTA revenue in this month looks booked net of commission. The figures below are normalised: rooms revenue is
          grossed up to the PMS and the expected commission added as a cost, so GOP is unchanged.
        </div>
      )}
      <div className="fin-tiles">
        <StatTile
          label="GOP"
          value={<Money value={m.gop} currency={ccy} />}
          sub={`${pctText(m.gop_margin_pct)} margin`}
          delta={changeDelta(m.gop, prev?.gop, true, vs)}
        />
        <StatTile
          label="GOP margin"
          value={pctText(m.gop_margin_pct)}
          sub={<>of <Money value={m.total_revenue} currency={ccy} compact /> total revenue</>}
          delta={ppDelta(m.gop_margin_pct, prev?.gop_margin_pct, true, vs)}
        />
        <StatTile
          label="GOPPAR"
          value={<Money value={m.goppar} currency={ccy} />}
          sub={<MarketChip mi={m.market_index} />}
          delta={changeDelta(m.goppar, prev?.goppar, true, vs)}
        />
        <StatTile
          label="TRevPAR"
          value={<Money value={m.trevpar} currency={ccy} />}
          sub={m.available_rn ? `${m.available_rn.toLocaleString("en")} available room nights` : "Available rooms n/a"}
          delta={changeDelta(m.trevpar, prev?.trevpar, true, vs)}
        />
        <StatTile
          label="RevPAR"
          value={<Money value={m.revpar} currency={ccy} />}
          sub={<>Rooms revenue <Money value={m.rooms_revenue} currency={ccy} compact /></>}
          delta={changeDelta(m.revpar, prev?.revpar, true, vs)}
        />
        <StatTile
          label="Flow-through"
          value={m.flow_through_pct === null ? NA : pctText(m.flow_through_pct)}
          sub={`Change in GOP over change in revenue, ${ftBasis}`}
        />
        <StatTile
          label="Labour cost"
          value={pctText(m.labour_pct)}
          sub={<>All payroll <Money value={m.payroll} currency={ccy} compact /> as a share of revenue</>}
          delta={ppDelta(m.labour_pct, prev?.labour_pct, false, vs)}
        />
        <StatTile
          label="Net profit"
          value={<Money value={m.net_profit} currency={ccy} />}
          sub={<>EBITDA <Money value={m.ebitda} currency={ccy} compact /></>}
          delta={changeDelta(m.net_profit, prev?.net_profit, true, vs)}
        />
      </div>
    </>
  );
}

// ─── GOPPAR market chip (context only, 6.7) ──────────────────────────────

export function MarketChip({ mi }: { mi?: MarketIndex }) {
  if (!mi || !mi.available) {
    return <span className="fin-small">Market comparison: {mi?.reason ?? "no public figure for this month"}.</span>;
  }
  return (
    <span className="fin-chip" title={mi.caveat}>
      <Badge tone="info">{mi.chip === "YTD" ? "YTD" : "Same month"}</Badge>
      <span>
        You {ratioText(mi.hotel_yoy)} vs market {ratioText(mi.market_yoy)} year on year
        {mi.relative ? `, relative ${ratioText(mi.relative)}` : ""}.
      </span>
      {mi.source_url && (
        <a className="fin-link" href={mi.source_url} target="_blank" rel="noopener noreferrer">
          {mi.publisher || "Source"} <LuExternalLink aria-hidden="true" size={11} />
        </a>
      )}
      {mi.caveat && <span className="fin-small" style={{ flexBasis: "100%" }}>{mi.caveat}</span>}
    </span>
  );
}
