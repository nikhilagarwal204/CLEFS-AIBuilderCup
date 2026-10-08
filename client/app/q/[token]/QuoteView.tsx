"use client";

/* The quote itself, presentational only (QuotePage owns data and calls).
   Money arrives as Decimal strings in the quote's currency and is only
   formatted here, never recomputed: the totals are the hotel's composed
   tax lines. Dates are stay dates (no timezone), formatted at UTC noon so
   no viewer's clock can shift them a day; the validity is a real instant
   and shows with its timezone name. */

import { depositLabel, fill, type QLang, type QStrings } from "../i18n";
import type { PublicQuote } from "./QuotePage";

// ─── Formatting ──────────────────────────────────────────────────────────

/** Latin digits in Arabic too: UAE prices and phone numbers are written
    with them, and a guest comparing with an OTA sees the same figures. */
const localeOf = (lang: QLang) => (lang === "ar" ? "ar-AE-u-nu-latn" : "en-AE");

function money(value: string | null | undefined, currency: string, lang: QLang): string {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return `${currency} ${value}`;
  try {
    return new Intl.NumberFormat(localeOf(lang), { style: "currency", currency, currencyDisplay: "code" }).format(n);
  } catch {
    return `${currency} ${value}`;
  }
}

/* Only an absolute https URL may become the Pay href. WHY: this page shares
   its origin with the console and the /api/clefs proxy, and React 18 still
   renders a javascript: href (it only warns). The server already rejects
   anything else; this is the second lock if a bad row ever slips through. */
function safePayUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && u.hostname ? u.href : null;
  } catch {
    return null;
  }
}

function day(iso: string | null | undefined, lang: QLang, weekday = false): string {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(localeOf(lang), {
    timeZone: "UTC", day: "numeric", month: "short", year: "numeric", ...(weekday ? { weekday: "short" } : {}),
  }).format(d);
}

/* In the hotel's timezone (the email and the console say the same time);
   the viewer's own clock only if the hotel's is missing or unknown. */
function instant(ts: string, lang: QLang, timeZone?: string | null): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const opts: Intl.DateTimeFormatOptions = {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
  };
  try {
    return new Intl.DateTimeFormat(localeOf(lang), { ...opts, ...(timeZone ? { timeZone } : {}) }).format(d);
  } catch {
    return new Intl.DateTimeFormat(localeOf(lang), opts).format(d);
  }
}

// ─── View ────────────────────────────────────────────────────────────────

export default function QuoteView({ q, t, lang, accepting, acceptError, result, waitingForLink, onAccept }: {
  q: PublicQuote;
  t: QStrings;
  lang: QLang;
  accepting: boolean;
  acceptError: string | null;
  result: { payment_reason?: string | null } | null;
  waitingForLink: boolean;
  onAccept: () => void;
}) {
  const c = q.currency;
  const m = (v: string | null | undefined) => money(v, c, lang);
  const closed = q.status === "expired" || q.status === "superseded" || q.status === "withdrawn";
  const payUrl = safePayUrl(q.pay_url);
  const notReady = !closed && !q.can_accept && !q.accepted;
  const mixed = new Set(q.lines.map(l => l.room_type_code ?? "")).size > 1;
  const lines = [...q.lines].sort((a, b) => a.stay_date.localeCompare(b.stay_date) || (a.room_type_code ?? "").localeCompare(b.room_type_code ?? ""));

  return (
    <>
      {closed && (
        <section className="inq-q__card" role="status">
          <h1>{q.status === "expired" ? t.expired : q.status === "superseded" ? t.superseded : t.withdrawn}</h1>
          <p className="inq-q__muted">{q.status === "expired" ? t.expiredBody : q.status === "superseded" ? t.supersededBody : ""}</p>
        </section>
      )}
      <section className={`inq-q__card${q.can_accept ? " inq-q__card--gold" : ""}`}>
        <h1>{t.title}</h1>
        <p className="inq-q__small">{fill(t.quoteRef, { ref: q.ref })}</p>
        {!closed && !q.accepted && (
          <span className="inq-q__pill">{fill(t.validUntil, { date: instant(q.valid_until, lang, q.hotel.timezone) })}</span>
        )}
        {q.accepted && <span className="inq-q__pill">{t.acceptedTitle}</span>}
        {notReady && <span className="inq-q__pill inq-q__pill--warn">{t.notReady}</span>}
        <div className="inq-q__stay">
          <div><span>{t.checkIn}</span><strong>{day(q.stay.check_in, lang, true)}</strong></div>
          <div><span>{t.checkOut}</span><strong>{day(q.stay.check_out, lang, true)}</strong></div>
          <div><span>&nbsp;</span><strong>{q.stay.nights === 1 ? t.night1 : q.stay.nights === 2 ? t.night2 : fill(t.nights, { n: q.stay.nights })}</strong></div>
          <div><span>&nbsp;</span><strong>{q.stay.rooms === 1 ? t.room1 : q.stay.rooms === 2 ? t.room2 : fill(t.rooms, { n: q.stay.rooms })}</strong></div>
        </div>
      </section>
      <section className="inq-q__card">
        <h2>{t.nightly}</h2>
        <table className="inq-q__table">
          <thead>
            <tr>
              <th scope="col">{t.night}</th>
              {mixed && <th scope="col">{t.roomType}</th>}
              <th scope="col" className="inq-q__amt">{t.rate}</th>
            </tr>
          </thead>
          <tbody>
            {lines.map(l => (
              <tr key={`${l.stay_date}|${l.room_type_code ?? ""}`}>
                <td>
                  {day(l.stay_date, lang, true)}
                  {!mixed && l.room_type_name && <span className="inq-q__sub">{l.room_type_name}{l.rooms > 1 ? ` x ${l.rooms}` : ""}</span>}
                </td>
                {mixed && <td>{l.room_type_name ?? l.room_type_code}{l.rooms > 1 ? ` x ${l.rooms}` : ""}</td>}
                <td className="inq-q__amt">{m(l.rate_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="inq-q__card">
        <h2>{t.priceBreakdown}</h2>
        <table className="inq-q__table">
          <tbody>
            <tr><td>{t.room}</td><td className="inq-q__amt">{m(q.taxes.room)}</td></tr>
            {Number(q.taxes.service_charge) > 0 && <tr><td>{t.serviceCharge}</td><td className="inq-q__amt">{m(q.taxes.service_charge)}</td></tr>}
            {Number(q.taxes.municipality_fee) > 0 && <tr><td>{t.municipalityFee}</td><td className="inq-q__amt">{m(q.taxes.municipality_fee)}</td></tr>}
            {Number(q.taxes.vat) > 0 && <tr><td>{t.vat}</td><td className="inq-q__amt">{m(q.taxes.vat)}</td></tr>}
            {Number(q.taxes.tourism_dirham) > 0 && (
              <tr>
                <td>{t.tourismDirham}<span className="inq-q__sub">{fill(t.tdNights, { n: q.taxes.td_nights_charged })}</span></td>
                <td className="inq-q__amt">{m(q.taxes.tourism_dirham)}</td>
              </tr>
            )}
            <tr className="inq-q__total"><td>{t.total}</td><td className="inq-q__amt">{m(q.taxes.total)}</td></tr>
          </tbody>
        </table>
        <p className="inq-q__small">{fill(t.inclusive, { currency: c })}</p>
        {q.fee_notices.length > 0 && (
          <ul className="inq-q__notes" lang="en" dir="ltr">{q.fee_notices.map((n, i) => <li key={i}>{n}</li>)}</ul>
        )}
      </section>
      {q.deposit_schedule.length > 0 && (
        <section className="inq-q__card">
          <h2>{t.schedule}</h2>
          <table className="inq-q__table">
            <tbody>
              {q.deposit_schedule.map(d => (
                <tr key={d.seq}>
                  <td>
                    {depositLabel(d.label, t)}
                    <span className="inq-q__sub">{d.due_on ? fill(t.dueOn, { date: day(d.due_on, lang) }) : t.dueNow}</span>
                    {d.td_note && <span className="inq-q__sub">{d.td_note}</span>}
                  </td>
                  <td className="inq-q__amt">{m(d.amount_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {(q.cancellation || q.inclusions || q.group_terms) && (
        <section className="inq-q__card">
          {q.group_terms && (
            <>
              <h2>{t.groupTerms}</h2>
              <p className="inq-q__terms">
                {t.cutoff}: {day(q.group_terms.cutoff_date, lang)}{"\n"}
                {fill(t.attrition, { pct: q.group_terms.attrition_pct, min: q.group_terms.min_rooms_after_attrition })}{"\n"}
                {t.roomingList}: {day(q.group_terms.rooming_list_due, lang)}
              </p>
            </>
          )}
          {q.cancellation && (<><h2>{t.cancellation}</h2><p className="inq-q__terms" dir="auto">{q.cancellation}</p></>)}
          {q.inclusions && (<><h2>{t.included}</h2><p className="inq-q__terms" dir="auto">{q.inclusions}</p></>)}
        </section>
      )}
      <div className="inq-q__actions" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {payUrl && !closed && (
          <>
            <a className="inq-q__btn" href={payUrl} rel="noopener noreferrer">
              {t.pay}
            </a>
            <p className="inq-q__small" style={{ textAlign: "center" }}>{t.payHint}</p>
          </>
        )}
        {q.can_accept && (
          <button type="button" className="inq-q__btn" disabled={accepting} onClick={onAccept}>
            {accepting ? t.accepting : t.accept}
          </button>
        )}
        {acceptError && <p className="inq-q__error" role="alert">{acceptError}</p>}
        {q.accepted && !payUrl && (
          <section className="inq-q__card" role="status" aria-live="polite">
            <h2>{t.acceptedTitle}</h2>
            <p className="inq-q__muted">{result?.payment_reason ? t.acceptedManual : t.acceptedBody}</p>
            {waitingForLink && <div className="inq-q__skel" style={{ width: "50%" }} aria-hidden="true" />}
          </section>
        )}
      </div>
    </>
  );
}
