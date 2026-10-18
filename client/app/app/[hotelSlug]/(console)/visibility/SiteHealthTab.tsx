"use client";

/* Site health tab (04 spec 5.6): Core Web Vitals from CrUX (phone, p75)
   and PageSpeed, the Hotel JSON-LD check and structured data compared with
   the knowledge base. Hygiene only: no ranking effect is claimed. */

import { hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Badge, Card, StatTile, Table, type Column } from "../../../../ui";
import type { Dec, SiteHealthCheck, SiteHealthView } from "./types";
import { num } from "./model";
import { SetupChecklist, TabState } from "./parts";

type FactRow = NonNullable<SiteHealthCheck["facts_vs_kb"]>[number];
type IssueRow = NonNullable<SiteHealthCheck["jsonld_issues"]>[number];

const ms = (v: Dec | undefined) => { const n = num(v ?? null); return n === null ? "n/a" : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`; };
/** Lighthouse performance score is 0..1; shown on the familiar 0-100 scale. */
const psiScore = (v: Dec | undefined) => { const n = num(v ?? null); return n === null ? "n/a" : String(Math.round(n * 100)); };
const cls = (v: Dec | undefined) => { const n = num(v ?? null); return n === null ? "n/a" : n.toFixed(2); };

function Vitals({ c, t }: { c: SiteHealthCheck; t: SiteHealthView["thresholds"] }) {
  const m = c.crux?.metrics ?? {};
  const over = new Set((c.crux?.outside_good ?? []).map(o => o.metric));
  const tone = (k: string) => (c.crux?.status !== "ok" ? undefined : over.has(k) ? "Needs work" : "Good");
  return (
    <div className="vis-tiles">
      <StatTile label="LCP (phone, p75)" value={ms(m.lcp_ms)} sub={`${tone("lcp_ms") ?? "no field data"}; good under ${ms(t.lcp_ms)}`} />
      <StatTile label="INP (phone, p75)" value={ms(m.inp_ms)} sub={`${tone("inp_ms") ?? "no field data"}; good under ${ms(t.inp_ms)}`} />
      <StatTile label="CLS (phone, p75)" value={cls(m.cls)} sub={`${tone("cls") ?? "no field data"}; good under ${cls(t.cls)}`} />
      <StatTile label="PageSpeed (mobile)" value={psiScore(c.psi?.score)}
        sub={c.psi ? `lab LCP ${ms(c.psi.lcp_ms)}, TBT ${ms(c.psi.tbt_ms)}` : "not measured"} />
    </div>
  );
}

export default function SiteHealthTab({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const r = useApi<SiteHealthView>(hotelPath(slug, "/visibility/site-health"), [slug]);

  const factCols: Column<FactRow>[] = [
    { key: "fact", header: "Fact", render: x => x.fact_key.replace(/_/g, " ") },
    { key: "ours", header: "Knowledge base", render: x => x.ours ?? "n/a" },
    { key: "theirs", header: "Website", render: x => x.theirs ?? "not found" },
    { key: "status", header: "Status", render: x => <Badge tone={x.status === "match" ? "ok" : x.status === "mismatch" ? "bad" : "neutral"}>{x.status}</Badge> },
  ];
  const issueCols: Column<IssueRow>[] = [
    { key: "sev", header: "Level", render: x => <Badge tone={x.severity === "required" ? "bad" : x.severity === "recommended" ? "warn" : "neutral"}>{x.severity}</Badge> },
    { key: "field", header: "Field", render: x => `${x.type}.${x.field}` },
    { key: "msg", header: "Issue", render: x => x.message },
  ];

  return (
    <TabState loading={r.loading && !r.data} error={r.error} onRetry={r.reload}>
      {r.data && (
        <div className="vis-stack">
          <SetupChecklist title="Set up site health" codes={r.data.setup_missing} onOpenSettings={onOpenSettings} />
          {!r.data.latest ? (
            <Card><p className="vis-muted">No check yet. Site health runs every Sunday at 03:00.</p></Card>
          ) : (
            <>
              <p className="vis-muted">Checked {fmt.dateTime(r.data.latest.checked_at)} · {r.data.latest.url}</p>
              {r.data.latest.crux?.status === "insufficient_data" && (
                <Card><p className="vis-muted">Google has too little field data for this site to report Core Web Vitals.</p></Card>
              )}
              <Vitals c={r.data.latest} t={r.data.thresholds} />
              <Card title="Hotel structured data" subtitle="JSON-LD on your home and room pages, as Google reads it."
                actions={<Badge tone={r.data.latest.jsonld_found ? "ok" : "warn"}>{r.data.latest.jsonld_found ? "Found" : "Missing"}</Badge>}>
                {(r.data.latest.jsonld_types ?? []).length > 0 && <p className="vis-muted">Types: {(r.data.latest.jsonld_types ?? []).join(", ")}</p>}
                <Table columns={issueCols} rows={r.data.latest.jsonld_issues ?? []} rowKey={(x, i) => `${x.url}-${x.field}-${i}`}
                  caption="Structured data issues" empty="No structured data issues." />
              </Card>
              <Card title="Website facts vs your knowledge base" subtitle="A mismatch proposes a website fix in your action list.">
                <Table columns={factCols} rows={r.data.latest.facts_vs_kb ?? []} rowKey={x => x.fact_key}
                  caption="Website facts compared with the knowledge base" empty="No comparable facts found on the website." />
              </Card>
            </>
          )}
          <p className="vis-note">{r.data.note}</p>
        </div>
      )}
    </TabState>
  );
}
