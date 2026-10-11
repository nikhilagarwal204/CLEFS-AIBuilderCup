"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Trust overview: one tile per section with the one number that says
   whether it needs attention today (the 22 tests, the shadow week, chain
   intact, concierge ready, privacy requests due, pilots on track). Each
   fetch runs only when the viewer holds that section's permission, so a GM
   without a grant sees no 403s.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Card, StatTile } from "../../../../ui";
import { dayLabel, PILOT_STATUS, trustApi, trustHref } from "./lib";
import { gateTestsText, shadowText } from "./pure";
import type { ChainState, Dsr, Pilot, Readiness } from "./types";

export default function TrustOverview() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();

  const chain = useApi<ChainState>(can("trust.audit.view") ? trustApi(slug, "/audit/chain") : null, [slug]);
  const ready = useApi<Readiness>(can(["trust.evals.view", "concierge.manage"]) ? trustApi(slug, "/readiness") : null, [slug]);
  const dsr = useApi<Dsr[]>(can("trust.privacy.manage") ? trustApi(slug, "/dsr?status=open") : null, [slug]);
  const pilots = useApi<Pilot[]>(can("trust.pilots.view") ? trustApi(slug, "/pilots") : null, [slug]);

  const overdue = (dsr.data ?? []).filter(d => d.overdue).length;
  const active = (pilots.data ?? []).filter(p => p.status === "active");
  const tests = gateTestsText(ready.data?.tests);
  const shadow = shadowText(ready.data?.shadow);

  return (
    <div className="cx-grid cx-grid--2">
      {can(["trust.evals.view", "concierge.manage"]) && (
        <Link className="trs-tile-link" href={trustHref(slug, "/evals")}>
          <StatTile
            label="Safety and accuracy tests"
            loading={ready.loading && !ready.data}
            value={ready.data ? tests.value : ready.error ? "Unavailable" : "-"}
            sub={ready.data ? tests.sub : "All 22 must pass before the concierge goes live, with Gemini as the judge."}
          />
        </Link>
      )}
      {can(["trust.evals.view", "concierge.manage"]) && (
        <Link className="trs-tile-link" href={trustHref(slug, "/evals")}>
          <StatTile
            label="Shadow mode"
            loading={ready.loading && !ready.data}
            value={ready.data ? shadow.value : ready.error ? "Unavailable" : "-"}
            sub={ready.data ? shadow.sub : "7 days of escalations worked out and shown before anyone is messaged."}
          />
        </Link>
      )}
      {can("trust.audit.view") && (
        <Link className="trs-tile-link" href={trustHref(slug, "/audit")}>
          <StatTile
            label="Audit log hash chain"
            loading={chain.loading && !chain.data}
            value={chain.data ? chainLabel(chain.data.status) : chain.error ? "Unavailable" : "-"}
            sub={chain.data?.verified_at
              ? `Last verified ${fmt.relative(chain.data.verified_at)}; ${fmt.number(chain.data.rows_checked ?? 0)} rows checked`
              : "Verified every night at 03:30. Open the log to verify now."}
          />
        </Link>
      )}
      {can(["trust.evals.view", "concierge.manage"]) && (
        <Link className="trs-tile-link" href={trustHref(slug, "/evals")}>
          <StatTile
            label="Concierge readiness"
            loading={ready.loading && !ready.data}
            value={ready.data ? (ready.data.ready ? (ready.data.overridden ? "Ready (override)" : "Ready") : "Not ready") : "-"}
            sub={ready.data
              ? `${ready.data.checks.filter(c => c.ok).length} of ${ready.data.checks.length} checks passing`
              : ready.error?.message ?? "Checks the knowledge base and the latest evaluation run."}
          />
        </Link>
      )}
      {can("trust.privacy.manage") && (
        <Link className="trs-tile-link" href={trustHref(slug, "/privacy")}>
          <StatTile
            label="Open privacy requests"
            loading={dsr.loading && !dsr.data}
            value={dsr.data ? fmt.number(dsr.data.length) : "-"}
            sub={overdue ? `${overdue} past the 30-day deadline` : "Each request is due 30 days after it arrives."}
          />
        </Link>
      )}
      {can("trust.pilots.view") && (
        <Link className="trs-tile-link" href={trustHref(slug, "/pilots")}>
          <StatTile
            label="Active pilots"
            loading={pilots.loading && !pilots.data}
            value={pilots.data ? fmt.number(active.length) : "-"}
            sub={active[0] ? `${active[0].name}: ends ${dayLabel(active[0].end_date, fmt.locale)}` : "Scorecards and printable ROI reports."}
          />
        </Link>
      )}
      {can("trust.pilots.view") && (pilots.data?.length ?? 0) > 0 && (
        <Card title="Recent pilots" className="trs-filters__wide">
          <div className="cx-stack" style={{ gap: 8 }}>
            {(pilots.data ?? []).slice(0, 4).map(p => (
              <div key={p.id} className="cx-row" style={{ justifyContent: "space-between" }}>
                <Link href={trustHref(slug, `/pilots/${p.id}`)}>{p.name}</Link>
                <Badge tone={PILOT_STATUS[p.status]?.tone ?? "neutral"}>{PILOT_STATUS[p.status]?.label ?? p.status}</Badge>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function chainLabel(s: ChainState["status"]): string {
  return s === "ok" ? "Intact" : s === "broken" ? "Break detected" : "Not verified yet";
}
