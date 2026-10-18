"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One week's verified-value report (GET /brief/reports/weekly/{week}),
   rendered from its payload so it reads in the console's own type and
   theme; the email's HTML is one toggle away (sandboxed, no scripts) and
   the PDF is the same file WhatsApp attaches. A week that has not ended
   answers 409 week_not_over, shown as "not yet" rather than an error.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useState } from "react";
import { LuArrowLeft, LuArrowRight, LuDownload } from "react-icons/lu";
import { API_PREFIX, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, EmptyState, MoneyText, Skeleton, StatTile } from "../../../../../ui";
import { brfApi, brfHref, eventLabel, shiftIsoWeek, statusLabel, statusTone } from "../lib";
import type { WeeklyReport } from "../types";

export default function WeeklyReportView({ week }: { week: string }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [showEmail, setShowEmail] = useState(false);
  const rep = useApi<WeeklyReport>(brfApi(slug, `/reports/weekly/${encodeURIComponent(week)}`), [slug, week]);
  const prev = shiftIsoWeek(week, -1);
  const next = shiftIsoWeek(week, 1);
  const pdfHref = `${API_PREFIX}${brfApi(slug, `/reports/weekly/${encodeURIComponent(week)}/pdf`)}`;

  const nav = (
    <div className="brf-bar">
      <div className="brf-bar__left">
        <Button size="sm" variant="ghost" href={brfHref(slug, "/reports")}>All reports</Button>
      </div>
      <div className="brf-bar__right">
        {prev && <Button size="sm" variant="ghost" icon={<LuArrowLeft />} href={brfHref(slug, `/reports/weekly/${prev}`)}>{prev}</Button>}
        {next && <Button size="sm" variant="ghost" href={brfHref(slug, `/reports/weekly/${next}`)}>{next} <LuArrowRight aria-hidden="true" /></Button>}
      </div>
    </div>
  );

  if (rep.loading && !rep.data) return <>{nav}<Card><Skeleton lines={5} /></Card></>;
  if (rep.error && !rep.data) {
    const code = rep.error.code;
    return (
      <>
        {nav}
        <EmptyState
          title={code === "week_not_over" ? `Week ${week} is not over yet` : code === "invalid_week" ? "That is not a week" : "Could not load the report"}
          body={code === "week_not_over" ? "The report is built the Monday after the week ends." : rep.error.message}
          action={code ? undefined : <Button onClick={rep.reload}>Try again</Button>} />
      </>
    );
  }
  if (!rep.data) return null;
  const p = rep.data.payload;
  const ccy = rep.data.currency;
  const maxType = Math.max(1, ...p.by_type.map(t => t.verified_minor));
  const d = p.decisions;

  return (
    <>
      {nav}
      <Card gold title={`Week ${p.period_key}`}
            subtitle={`${fmt.date(p.period_start + "T12:00:00Z", "long")} to ${fmt.date(p.period_end + "T12:00:00Z", "long")}`}
            actions={rep.data.pdf_file_id ? (
              <a className="cx-btn cx-btn--secondary cx-btn--sm" href={pdfHref} download>
                <LuDownload aria-hidden="true" /> PDF
              </a>
            ) : undefined}>
        <p style={{ margin: 0 }}>
          <MoneyText amount={p.verified_minor} minor currency={ccy} /> verified of{" "}
          <MoneyText amount={p.identified_minor} minor currency={ccy} /> identified across {p.identified_signals} findings.
        </p>
      </Card>

      <div className="brf-tiles">
        <StatTile label="Verified" value={<MoneyText amount={p.verified_minor} minor currency={ccy} />} sub="caused by an action, confirmed" />
        <StatTile label="Identified" value={<MoneyText amount={p.identified_minor} minor currency={ccy} />} sub={`${p.identified_signals} findings`} />
        <StatTile label="Decisions" value={fmt.number(d.count)}
                  sub={d.median_hours_to_decision !== null ? `median ${d.median_hours_to_decision} h to decide` : undefined} />
        <StatTile label="Approved" value={d.approval_rate !== null ? fmt.pct(d.approval_rate) : "-"} sub="of approve or ignore decisions" />
      </div>

      <div className="brf-grid2">
        <Card title="Verified by type">
          {p.by_type.length ? (
            <div className="brf-bars">
              {p.by_type.map(t => (
                <div key={t.type} className="brf-bars__row">
                  <span>{t.label} <span className="brf-muted">({t.actions})</span></span>
                  <span className="brf-bars__track" aria-hidden="true">
                    <span className="brf-bars__fill" style={{ display: "block", width: `${Math.round((100 * t.verified_minor) / maxType)}%` }} />
                  </span>
                  <MoneyText amount={t.verified_minor} minor currency={ccy} />
                </div>
              ))}
            </div>
          ) : <p className="brf-muted" style={{ margin: 0 }}>Nothing was verified this week.</p>}
          {Object.keys(d.by_event).length > 0 && (
            <p className="brf-muted" style={{ margin: "12px 0 0" }}>
              {Object.entries(d.by_event).map(([k, n]) => `${eventLabel(k)} ${n}`).join(" · ")}
            </p>
          )}
        </Card>

        <Card title="Overdue now">
          {p.overdue.length ? (
            <ul className="brf-pick">
              {p.overdue.map(o => (
                <li key={o.action_id} className="brf-row" style={{ justifyContent: "space-between" }}>
                  <Link className="cx-link" href={brfHref(slug, `/actions/${o.action_id}`)}>{o.title}</Link>
                  <Badge tone={statusTone(o.status)}>{statusLabel(o.status)}</Badge>
                </li>
              ))}
            </ul>
          ) : <p className="brf-muted" style={{ margin: 0 }}>Nothing overdue.</p>}
        </Card>

        <Card title="Largest open items">
          {p.top_open.length ? (
            <ul className="brf-pick">
              {p.top_open.map(t => (
                <li key={t.signal_id} className="brf-row" style={{ justifyContent: "space-between" }}>
                  <Link className="cx-link" href={brfHref(slug, `?signal=${t.signal_id}`)}>{t.title}</Link>
                  {t.impact_value && <MoneyText amount={t.impact_value} currency={t.currency} />}
                </li>
              ))}
            </ul>
          ) : <p className="brf-muted" style={{ margin: 0 }}>No open money items.</p>}
        </Card>
      </div>

      {rep.data.html && (
        <Card title="Email version" actions={<Button size="sm" variant="ghost" onClick={() => setShowEmail(s => !s)}>{showEmail ? "Hide" : "Show"}</Button>}>
          {showEmail && (
            <iframe title={`Weekly report ${week}, email version`} sandbox="" srcDoc={rep.data.html}
                    style={{ width: "100%", minHeight: 640, border: 0, borderRadius: 12, background: "#fff" }} />
          )}
        </Card>
      )}
    </>
  );
}
