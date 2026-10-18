"use client";

/* Google Business Profile block of the Google tab (04 spec 5.4): the
   connection state (project not approved / not connected / pick location /
   connected), derived monthly KPIs only (raw daily rows expire), keywords
   with threshold rows shown as "< N" and never summed, the review reply
   stats from Reviews with "Draft replies" (reviews.api.draft_reply through
   POST /visibility/gbp/draft-replies; Visibility never posts a reply), and
   the UTM websiteUri patch and lodging sync. */

import Link from "next/link";
import { useState } from "react";
import { apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, StatTile, Table, useToast, type Column, type StatDelta } from "../../../../ui";
import type { DraftRepliesOut, GbpKeyword, GbpView } from "./types";
import { GBP_STATE_TEXT, draftRepliesNotice, num } from "./model";
import { errText } from "./parts";

// ─── Connect ─────────────────────────────────────────────────────────────

/** Core OAuth start (CONTRACTS "OAuth"): fetch the authorize URL, then leave. */
export async function startGbpConnect(slug: string): Promise<void> {
  const next = `/app/${encodeURIComponent(slug)}/visibility`;
  const out = await apiFetch<{ authorize_url: string }>(hotelPath(slug, "/oauth/google_business/start"), {
    query: { format: "json", next },
  });
  window.location.assign(out.authorize_url);
}

function delta(cur: number | null, prev: number | null): StatDelta | undefined {
  if (cur === null || prev === null || prev === 0) return undefined;
  const pct = ((cur - prev) / prev) * 100;
  return { value: `${pct > 0 ? "+" : ""}${pct.toFixed(0)}%`, direction: pct > 0 ? "up" : pct < 0 ? "down" : "flat", good: pct >= 0, label: "vs prior 28 days" };
}

// ─── Section ─────────────────────────────────────────────────────────────

export default function GbpSection({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const fmt = useFormat();
  const g = useApi<GbpView>(hotelPath(slug, "/visibility/gbp"), [slug]);
  const [busy, setBusy] = useState<null | "connect" | "utm" | "lodging" | "drafts">(null);
  const [drafted, setDrafted] = useState(false);

  if (!g.data) return g.error ? <Card title="Google Business Profile"><p className="vis-muted">{errText(g.error)}</p></Card> : null;
  const v = g.data;
  const st = GBP_STATE_TEXT[v.state.status] ?? GBP_STATE_TEXT.not_connected;
  const k = v.kpis[0] ?? null;

  const connect = async () => {
    setBusy("connect");
    try { await startGbpConnect(slug); } catch (err) { toast.error("Could not start the Google connection", errText(err)); setBusy(null); }
  };
  const propose = async (kind: "utm" | "lodging") => {
    setBusy(kind);
    try {
      const out = await apiFetch<Record<string, unknown>>(hotelPath(slug, `/visibility/actions/${kind === "utm" ? "utm-patch" : "lodging-sync"}`), { method: "POST" });
      if (kind === "lodging" && out.diff === 0) toast.info("Google already matches your knowledge base");
      else if (out.status === "suppressed") toast.info("Not proposed", "The autonomy dial suppresses this action type.");
      else toast.success("Proposed", "The change waits for approval in your action list, unless the dial runs it.");
      g.reload();
    } catch (err) {
      toast.error("Could not propose the change", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const draftReplies = async () => {
    setBusy("drafts");
    try {
      const out = await apiFetch<DraftRepliesOut>(hotelPath(slug, "/visibility/gbp/draft-replies"), { method: "POST" });
      const n = draftRepliesNotice(out);
      toast[n.tone](n.title, n.body);
      setDrafted(out.drafted > 0);
    } catch (err) {
      toast.error("Could not draft replies", errText(err));
    } finally {
      setBusy(null);
    }
  };
  const unanswered = v.review_replies?.unanswered ?? 0;
  const reviewsHref = `/app/${encodeURIComponent(slug)}/reviews?tab=replies`;

  const kwCols: Column<GbpKeyword>[] = [
    { key: "keyword", header: "Search term", render: r => <span>{r.keyword} {r.is_branded && <Badge tone="gold">Brand</Badge>}</span> },
    { key: "value", header: "Searches", align: "right", render: r => <span className="vis-num">{r.display}</span> },
  ];

  return (
    <Card title="Google Business Profile" subtitle={st.hint}
      actions={<Badge tone={st.tone} dot>{st.label}</Badge>}>
      {v.state.status === "not_connected" && can("visibility.manage") && (
        <Button variant="primary" onClick={() => void connect()} loading={busy === "connect"}>Connect Google</Button>
      )}
      {v.state.status === "pick_location" && <Button onClick={onOpenSettings}>Pick location</Button>}
      {v.state.status === "connected" && (
        <div className="vis-stack">
          <div className="vis-tiles">
            <StatTile label={`Reach (${v.reach_label})`} value={fmt.number(k?.reach_user_days ?? null)}
              delta={delta(k?.reach_user_days ?? null, k?.reach_prior_user_days ?? null)}
              sub={k ? `${fmt.date(k.month)}; year ago ${fmt.number(k.reach_yoy_user_days)}` : "Computed on day 3"} />
            <StatTile label="Website clicks" value={fmt.number(k?.website_clicks ?? null)} sub="from the profile" />
            <StatTile label="Action rate" value={fmt.pct(num(k?.action_rate ?? null), { ratio: true })} sub="actions per user-day reached" />
            <StatTile label="Replies to Google reviews"
              value={v.review_replies ? fmt.pct(num(v.review_replies.response_rate ?? null), { digits: 0 }) : "n/a"}
              sub={v.review_replies ? `${v.review_replies.unanswered ?? 0} unanswered, last 90 days` : "From Reviews once synced"} />
          </div>
          {v.review_replies && (unanswered > 0 || drafted) && (
            <div className="vis-row">
              {can("visibility.act") && unanswered > 0 && (
                <Button size="sm" onClick={() => void draftReplies()} loading={busy === "drafts"}>Draft replies</Button>
              )}
              <Link href={reviewsHref} className="vis-link">Open replies in Reviews</Link>
              <span className="vis-muted">Drafts wait for your approval in Reviews; nothing is posted from here.</span>
            </div>
          )}
          <div className="vis-two">
            <div>
              <h3 className="cx-card__title" style={{ marginBottom: 8 }}>Search terms {v.keywords?.month ? `(${fmt.date(v.keywords.month)})` : ""}</h3>
              <Table columns={kwCols} rows={v.keywords?.rows ?? []} rowKey={r => r.keyword} caption="Business Profile search terms"
                empty="Keywords arrive monthly on day 5." />
              {v.keywords?.summary && (
                <p className="vis-note">Total: {v.keywords.summary.display}. Branded share {fmt.pct(num(v.keywords.summary.branded_share), { ratio: true, digits: 0 })}. Google hides exact counts below a threshold; those rows are never added up.</p>
              )}
            </div>
            <div className="vis-stack">
              <div>
                <h3 className="cx-card__title">Tagged website link</h3>
                <p className="vis-muted">Adds UTM tags to the profile&apos;s website link so Google bookings show up in attribution.</p>
                <div className="vis-row" style={{ marginTop: 8 }}>
                  {v.utm?.status ? <Badge tone={v.utm.status === "verified" ? "ok" : "info"}>{v.utm.status.replace(/_/g, " ")}</Badge> : <Badge>Not tagged</Badge>}
                  {can("visibility.act") && (
                    <Button size="sm" onClick={() => void propose("utm")} loading={busy === "utm"}>Tag website link</Button>
                  )}
                </div>
              </div>
              <div>
                <h3 className="cx-card__title">Hotel facts on Google</h3>
                <p className="vis-muted">Compares Google&apos;s lodging facts with your knowledge base and proposes a sync.</p>
                {can("visibility.act") && (
                  <Button size="sm" onClick={() => void propose("lodging")} loading={busy === "lodging"} style={{ marginTop: 8 }}>Check and sync facts</Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
