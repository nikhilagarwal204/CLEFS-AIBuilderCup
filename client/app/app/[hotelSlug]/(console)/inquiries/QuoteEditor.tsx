"use client";

/* One quote version (4.6, 4.7): the per-night grid against its guardrail
   band (floor, BAR, ceiling), the tax lines exactly as the server composed
   them (one compose_stay call, so the Tourism Dirham cap and per-bedroom
   rows are right), the terms, and why it cannot auto-send.

   Rates are typed in major units and converted to minor units exactly
   (lib.majorToMinor). The server is the authority: it clamps, refuses a
   rate above the ceiling, and allows below the floor only to an approver
   with a reason. Editing cancels a pending send (a fresh decision is
   needed); a sent quote is locked and changes need a new version. */

import { useMemo, useState } from "react";
import { api } from "../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Field, Input, useToast } from "../../../../ui";
import { NightGrid, QuoteTerms, TaxLines } from "./QuoteParts";
import { autoSendText, errorText, inqPath, lineKey, majorToMinor, minorToInput, QUOTE_STATUS_LABEL, rateBand } from "./lib";
import type { InquiryDetail, Quote, SendResult } from "./types";

const EDITABLE = new Set(["draft", "awaiting_approval", "approved"]);

export default function QuoteEditor({ row, quote, onChanged }: {
  row: InquiryDetail;
  quote: Quote;
  onChanged: () => void;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const digits = currencyDigits(quote.currency);
  const editable = EDITABLE.has(quote.status) && can("inquiries.quote");
  const canApprove = can("inquiries.approve");

  // Typed values keyed by night + room type; absent = unchanged.
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"save" | "send" | "request" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const edits = useMemo(() => {
    const out: { stay_date: string; room_type_code: string | null; rate_minor: number }[] = [];
    let invalid = false;
    let belowFloor = false;
    for (const ln of quote.lines) {
      const t = typed[lineKey(ln)];
      if (t === undefined) continue;
      const minor = majorToMinor(t, digits);
      if (minor === null || minor <= 0) { invalid = true; continue; }
      if (minor === ln.rate_minor) continue;
      if (rateBand(ln, minor) === "below_floor") belowFloor = true;
      out.push({ stay_date: ln.stay_date, room_type_code: ln.room_type_code, rate_minor: minor });
    }
    return { out, invalid, belowFloor };
  }, [typed, quote.lines, digits]);

  const dirty = edits.out.length > 0;
  const missing = quote.lines.some(l => l.rate_minor == null);

  async function save() {
    setBusy("save");
    setError(null);
    try {
      await api.put(inqPath(slug, `/${row.id}/quotes/${quote.version}`), {
        edits: edits.out, reason: reason.trim() || undefined,
      });
      setTyped({});
      toast.success("Rates saved", "Taxes and the deposit schedule were recalculated.");
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function send(requestApproval: boolean) {
    setBusy(requestApproval ? "request" : "send");
    setError(null);
    try {
      const res = await api.post<SendResult>(inqPath(slug, `/${row.id}/quotes/${quote.version}/send`),
        { request_approval: requestApproval });
      const st = res.action?.status;
      if (st === "proposed") toast.info("Waiting for approval", "An approver will see it in the brief and here.");
      else if (st === "observed") toast.info("Logged, not sent", "Quote sending is set to observe in the autonomy settings.");
      else toast.success("Quote on its way", "It goes on WhatsApp inside the window, otherwise as a template or email.");
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="inq-row inq-row--between">
        <span className="inq-row">
          <strong>{row.ref.toUpperCase()}-v{quote.version}</strong>
          <Badge tone={quote.status === "sent" || quote.status === "accepted" ? "ok" : quote.status === "awaiting_approval" ? "warn" : "neutral"}>
            {QUOTE_STATUS_LABEL[quote.status]}
          </Badge>
          {quote.language === "ar" && <Badge>Arabic</Badge>}
          {quote.below_bar && <Badge tone="warn">Below BAR</Badge>}
          {Number(quote.max_discount_pct) > 0 && <Badge tone="warn">Up to {quote.max_discount_pct}% off</Badge>}
        </span>
        <span className="inq-muted">
          Valid until {fmt.dateTime(quote.valid_until, "medium")}
          {quote.sent_at ? `, sent ${fmt.relative(quote.sent_at)}` : ""}
        </span>
      </div>
      {(quote.status === "sent" || quote.status === "accepted" || quote.status === "expired") && (
        <p className="inq-muted" style={{ margin: 0 }}>This version went to the guest, so it is locked. Build a new version to change it.</p>
      )}
      {quote.status === "superseded" && (
        <p className="inq-muted" style={{ margin: 0 }}>A newer version replaced this one.</p>
      )}
      <NightGrid quote={quote} typed={typed} editable={editable} digits={digits}
        onType={(k, v) => setTyped(prev => ({ ...prev, [k]: v }))} toInput={m => minorToInput(m, digits)} />
      {editable && (dirty || edits.invalid) && (
        <div className="inq-note" style={{ display: "grid", gap: 10 }}>
          {edits.invalid && <span>Some rates are not valid amounts (use digits and at most {digits} decimals).</span>}
          {edits.belowFloor && (
            canApprove ? (
              <Field label="Reason for going below the floor" required
                hint="Recorded with the quote; the owner sees it in the pricing trace.">
                <Input value={reason} maxLength={500} onChange={e => setReason(e.target.value)} />
              </Field>
            ) : (
              <span>A rate is below the floor. Only an approver (owner, GM or revenue) can quote below it.</span>
            )
          )}
          <div className="inq-row">
            <Button variant="primary" loading={busy === "save"}
              disabled={!dirty || edits.invalid || (edits.belowFloor && (!canApprove || !reason.trim())) || busy !== null}
              onClick={save}>Save rates</Button>
            <Button variant="ghost" disabled={busy !== null} onClick={() => { setTyped({}); setReason(""); }}>Discard</Button>
          </div>
        </div>
      )}
      <TaxLines quote={quote} dirty={dirty} />
      <QuoteTerms quote={quote} />
      {quote.auto_send_reasons.length > 0 && EDITABLE.has(quote.status) && (
        <section className="inq-section">
          <h3>Needs a person because</h3>
          <ul className="inq-list">
            {quote.auto_send_reasons.map(r => <li key={r}>{autoSendText(r)}</li>)}
          </ul>
        </section>
      )}
      {EDITABLE.has(quote.status) && can("inquiries.quote") && (
        <div className="inq-row">
          {canApprove ? (
            <Button variant="primary" loading={busy === "send"} disabled={dirty || missing || busy !== null}
              onClick={() => send(false)}>
              {quote.status === "awaiting_approval" ? "Approve and send" : "Send to guest"}
            </Button>
          ) : (
            <Button variant="primary" loading={busy === "send"} disabled={dirty || missing || busy !== null || quote.status === "awaiting_approval"}
              onClick={() => send(false)}>
              {quote.auto_send_eligible ? "Send to guest" : "Request approval"}
            </Button>
          )}
          {canApprove && quote.status !== "awaiting_approval" && (
            <Button variant="ghost" loading={busy === "request"} disabled={dirty || missing || busy !== null}
              onClick={() => send(true)}>Ask another approver</Button>
          )}
          {quote.status === "awaiting_approval" && <span className="inq-muted">Waiting for approval.</span>}
          {missing && <span className="inq-muted">Enter every night&apos;s rate first.</span>}
          {dirty && <span className="inq-muted">Save the rates first.</span>}
        </div>
      )}
      {error && <p className="inq-note inq-note--bad" role="alert">{error}</p>}
    </div>
  );
}
