"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The owner's mobile brief (/brief/[token], 02 spec 6.4, Canon 14): one
   column, the headline and money at stake, overdue pins then the ranked
   items, each with one-tap decisions, and the verified line.

   Reading never spends the link: GET /public/brief/{token} only looks the
   token up (72 h, scoped to this run's action ids). "Open in Clefs" is the
   one step that spends a use: a POST to /auth/magic/exchange (never on
   page load, so WhatsApp's preview fetcher cannot burn it) trades it for a
   12 h console session scoped to this hotel, then lands on the full brief.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, api, useApi } from "../../lib/api";
import { makeFormatter } from "../../lib/format";
import { loginHref, safeNextPath } from "../../lib/session";
import type { MagicExchangeResponse } from "../../lib/types";
import { UiStyles } from "../../ui";
import { mStyles } from "../styles";
import MobileItemCard from "./MobileItemCard";
import type { MobileAction, MobileBriefData, MobileItem } from "./types";

function errorCopy(err: ApiError): { title: string; body: string; retry: boolean } {
  if (err.status === 401 || err.code === "magic_link_invalid") {
    return { title: "This link has expired", body: "Brief links work for 72 hours. Open the latest brief, or sign in to Clefs.", retry: false };
  }
  if (err.status === 429) return { title: "Too many taps", body: "Wait a minute, then try again.", retry: true };
  if (err.status === 0) return { title: "Can't reach Clefs", body: "Check your connection, then try again. The link is still valid.", retry: true };
  return { title: "Something went wrong", body: "Clefs could not open this brief. Try again in a moment.", retry: true };
}

export default function MobileBrief({ token }: { token: string }) {
  const router = useRouter();
  const res = useApi<MobileBriefData>(`/public/brief/${encodeURIComponent(token)}`, [token]);
  const [now] = useState(() => Date.now());
  // Decisions made on this page, keyed by action id (the run is a snapshot).
  const [views, setViews] = useState<Record<number, MobileAction>>({});
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const exchanging = useRef(false);

  const d = res.data;
  const fmt = useMemo(() => makeFormatter(d?.hotel.currency ?? "AED", d?.hotel.timezone ?? "Asia/Dubai", d?.hotel.locale ?? "en-AE"), [d]);
  const withView = useCallback((i: MobileItem): MobileItem => (
    i.action && views[i.action.id] ? { ...i, action: views[i.action.id] } : i
  ), [views]);
  const onUpdated = useCallback((v: MobileAction) => setViews(s => ({ ...s, [v.id]: v })), []);

  /** POST the token for a console session, then go to the full brief (or one item). */
  const openConsole = useCallback(async (actionId: number | null) => {
    if (exchanging.current) return;
    exchanging.current = true;
    setOpening(true);
    setOpenError(null);
    try {
      const out = await api.post<MagicExchangeResponse>("/auth/magic/exchange", { token }, { silent401: true });
      const slug = out.hotel_slug || d?.hotel.slug;
      const base = safeNextPath(out.next_path, slug ? `/app/${encodeURIComponent(slug)}/brief` : "/login");
      const target = actionId !== null && slug ? `/app/${encodeURIComponent(slug)}/brief/actions/${actionId}` : base;
      // replace, not push: the token URL must not stay in history.
      router.replace(target);
    } catch (err) {
      exchanging.current = false;
      setOpening(false);
      setOpenError(err instanceof ApiError && err.status === 401
        ? "This link has been used too many times. Sign in to Clefs instead."
        : "Could not open Clefs. Try again in a moment.");
    }
  }, [token, router, d]);

  let body: ReactNode;
  if (res.loading && !d) {
    body = (
      <div aria-busy="true" style={{ display: "grid", gap: 12 }}>
        <div className="brf-m__skel" style={{ height: 90 }} />
        <div className="brf-m__skel" />
        <div className="brf-m__skel" />
      </div>
    );
  } else if (res.error && !d) {
    const c = errorCopy(res.error);
    body = (
      <section className="brf-m__card">
        <h1>{c.title}</h1>
        <p className="brf-m__muted">{c.body}</p>
        <div className="brf-m__btns">
          {c.retry && <button type="button" className="brf-m__btn brf-m__btn--primary brf-m__btn--wide" onClick={res.reload}>Try again</button>}
          <a className={`brf-m__btn brf-m__btn--wide${c.retry ? "" : " brf-m__btn--primary"}`} href={loginHref()}
             style={{ display: "grid", placeItems: "center", textDecoration: "none" }}>Sign in to Clefs</a>
        </div>
      </section>
    );
  } else if (d) {
    body = <BriefBody d={d} fmt={fmt} now={now} token={token} withView={withView} onUpdated={onUpdated}
                      onOpen={openConsole} opening={opening} openError={openError} />;
  }

  return (
    <main className="brf-m">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: mStyles }} />
      <div className="brf-m__wrap">{body}</div>
    </main>
  );
}

// ─── The brief itself ────────────────────────────────────────────────────

const TIER: Record<string, string> = { daily: "Daily brief", weekly: "Weekly brief", monthly: "Monthly brief" };

function BriefBody({ d, fmt, now, token, withView, onUpdated, onOpen, opening, openError }: {
  d: MobileBriefData;
  fmt: ReturnType<typeof makeFormatter>;
  now: number;
  token: string;
  withView: (i: MobileItem) => MobileItem;
  onUpdated: (v: MobileAction) => void;
  onOpen: (actionId: number | null) => void;
  opening: boolean;
  openError: string | null;
}) {
  const r = d.run;
  const first = d.user.name?.split(" ")[0] || "there";
  const card = (i: MobileItem) => (
    <MobileItemCard key={`${i.kind}-${i.item_no}`} token={token} item={withView(i)} fmt={fmt} now={now}
                    onUpdated={onUpdated} onOpenConsole={onOpen} />
  );
  return (
    <>
      <header style={{ display: "grid", gap: 4 }}>
        <p className="brf-m__hotel">{d.hotel.name}</p>
        <p className="brf-m__hi">Hello {first}. {TIER[r.cadence] ?? "Brief"} for {fmt.date(r.brief_date + "T12:00:00Z", "long")}.</p>
      </header>

      <section className="brf-m__card brf-m__card--gold" aria-label="Summary">
        <h1>{r.headline}</h1>
        {r.at_stake_minor ? <div className="brf-m__stake">{fmt.money(r.at_stake_minor, { minor: true, currency: r.currency })} <span className="brf-m__muted">at stake</span></div> : null}
      </section>

      {r.overdue.length > 0 && <h2 className="brf-m__section">Overdue</h2>}
      {r.overdue.map(card)}
      {r.items.length > 0 && <h2 className="brf-m__section">{r.overdue.length ? "Today" : "Ranked by money and deadline"}</h2>}
      {r.items.map(card)}
      {!r.items.length && !r.overdue.length && (
        <section className="brf-m__card"><p>Nothing needs your decision. Enjoy the quiet.</p></section>
      )}

      {r.verified?.text && <p className="brf-m__verified">{r.verified.text}.</p>}

      <div className="brf-m__btns">
        <button type="button" className="brf-m__btn brf-m__btn--wide" disabled={opening} onClick={() => onOpen(null)}>
          {opening ? "Opening Clefs..." : "Open the full brief in Clefs"}
        </button>
      </div>
      {openError && <p className="brf-m__err" role="alert">{openError}</p>}
      <p className="brf-m__small">
        This private link works until {fmt.dateTime(d.expires_at, "medium")}. Do not forward it: decisions made here are recorded as yours.
      </p>
    </>
  );
}
