"use client";

/* The thread and the reply composer (4.11). The composer knows the
   channels' rules instead of letting a send fail later:
   - WhatsApp free text only inside 24 h of the guest's last message; after
     that only an approved template can go, which is what "Send quote" uses;
   - email whenever the inquiry has an address;
   - a phone-desk inquiry with neither gets "call the guest".
   A staff reply is POST /{id}/messages (the server creates the
   `inquiries.respond` action, approved by this person, and runs it). An AI
   draft is POST /{id}/draft-reply and is governed by the autonomy dial.
   Prices never go in a message: they come only from quotes (the server
   lints AI drafts; here a typed price gets a warning). */

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Field, Segmented, Textarea, useToast } from "../../../../ui";
import { DRAFT_OFF_CODES, errorText, inqPath, isMissingRoute, windowOpen } from "./lib";
import type { InquiryDetail, Message } from "./types";

const AUTHOR: Record<Message["author_type"], string> = {
  guest: "Guest", staff: "Staff", ai: "Clefs AI", system: "Automatic",
};

/** A money figure typed into a reply ("AED 450", "450 aed", "450 درهم"). */
const PRICE_RE = /(\b(aed|usd|eur|gbp|sar|dhs?)\s?\d)|(\d[\d,.]*\s?(aed|usd|eur|gbp|sar|dhs?|درهم)\b)|(د\.إ)/i;

type Channel = "whatsapp" | "email";

export default function ThreadPanel({ row, onChanged }: { row: InquiryDetail; onChanged: () => void }) {
  const fmt = useFormat();
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [row.messages.length]);

  return (
    <div>
      <div className="inq-thread" aria-live="polite">
        {row.messages.length === 0 && <p className="inq-muted">No messages on this inquiry.</p>}
        {row.messages.map(m => {
          const cls = m.author_type === "system" ? "inq-msg inq-msg--system" : m.direction === "out" ? "inq-msg inq-msg--out" : "inq-msg";
          return (
            <div key={m.id} className={cls}>
              <div className="inq-msg__body" dir="auto">{m.body || (m.template_name ? `Template ${m.template_name}` : "(no text)")}</div>
              <div className="inq-msg__meta">
                <span>{AUTHOR[m.author_type] ?? m.author_type}</span>
                <span>{m.channel}</span>
                {m.template_name && <span>template {m.template_name}</span>}
                <span title={fmt.dateTime(m.created_at, "medium")}>{fmt.relative(m.created_at)}</span>
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
      <Composer row={row} onChanged={onChanged} />
    </div>
  );
}

// ─── Composer ────────────────────────────────────────────────────────────

function Composer({ row, onChanged }: { row: InquiryDetail; onChanged: () => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const waOpen = row.has_phone && windowOpen(row.wa_window_expires_at);
  const options = useMemo(() => [
    { value: "whatsapp" as Channel, label: "WhatsApp", disabled: !waOpen },
    { value: "email" as Channel, label: "Email", disabled: !row.has_email },
  ], [waOpen, row.has_email]);
  const [channel, setChannel] = useState<Channel>(waOpen ? "whatsapp" : "email");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"send" | "draft" | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  // AI drafts ship separately from replies: a missing draft route hides only
  // its button, never the composer (the reply route may well exist).
  const [draftOff, setDraftOff] = useState(false);

  const usable = options.filter(o => !o.disabled);
  const effective: Channel | null = usable.some(o => o.value === channel) ? channel : usable[0]?.value ?? null;
  const priceWarn = PRICE_RE.test(text);
  if (!can("inquiries.respond")) return null;

  async function send() {
    if (!effective || !text.trim()) return;
    setBusy("send");
    try {
      await api.post(inqPath(slug, `/${row.id}/messages`), { body: text.trim(), channel: effective });
      setText("");
      toast.success("Reply sent");
      onChanged();
    } catch (err) {
      if (isMissingRoute(err)) setUnavailable(true);
      else toast.error("Reply not sent", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function draft() {
    setBusy("draft");
    try {
      const res = await api.post<Record<string, unknown>>(inqPath(slug, `/${row.id}/draft-reply`),
        effective ? { channel: effective } : {});
      const drafted = draftText(res);
      if (drafted) {
        setText(drafted);
        toast.info("Draft ready", "Read it, change what you like, then send.");
      } else {
        toast.info("Draft created", "It is waiting for approval with the other actions.");
      }
      onChanged();
    } catch (err) {
      const code = (err as { code?: string } | null)?.code ?? "";
      if (isMissingRoute(err)) {
        setDraftOff(true);
        toast.info("AI drafts are not switched on yet", "Write the reply yourself for now.");
      } else if (DRAFT_OFF_CODES.includes(code)) {
        setDraftOff(true);
        toast.info("No AI draft for this inquiry",
          code === "dial_off" ? "AI reply drafts are switched off in the autonomy settings." : errorText(err));
      } else toast.error("No draft", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  if (unavailable) {
    return (
      <p className="inq-note" style={{ marginTop: 12 }}>
        Replying from the console is not switched on for this hotel yet. Reply from your WhatsApp Business app or
        email for now; quotes and payment links still go from here.
      </p>
    );
  }

  return (
    <div className="inq-section">
      <h3>Reply</h3>
      {usable.length === 0 ? (
        <p className="inq-note inq-note--warn">
          {row.has_phone
            ? "The WhatsApp window is closed and there is no email address. Send a quote (it goes as an approved template), or call the guest."
            : "No way to message this guest. Call them, or add an email address in Details."}
        </p>
      ) : (
        <>
          <div className="inq-row">
            <Segmented size="sm" ariaLabel="Reply channel" options={options} value={effective ?? "email"}
              onChange={setChannel} />
            {!waOpen && row.has_phone && <Badge>WhatsApp window closed: template only</Badge>}
          </div>
          <Field label="Message" hint={effective === "whatsapp" ? "Goes as a WhatsApp message." : "Goes by email from the hotel's address."}
            error={priceWarn ? "Prices belong in a quote (taxes, validity and terms go with it). Use the Quote tab." : undefined}>
            <Textarea rows={4} value={text} maxLength={4000} dir="auto" onChange={e => setText(e.target.value)}
              placeholder="Thank you for your message..." />
          </Field>
          <div className="inq-row">
            <Button variant="primary" loading={busy === "send"} disabled={!text.trim() || busy !== null} onClick={send}>
              Send reply
            </Button>
            {!draftOff && (
              <Button variant="ghost" loading={busy === "draft"} disabled={busy !== null} onClick={draft}>
                Draft with AI
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** The drafted text from whatever shape the draft route returns:
    {body} | {text} | {action: {params: {body|text}}}. */
function draftText(res: Record<string, unknown> | undefined): string | null {
  if (!res) return null;
  const direct = res.body ?? res.text;
  if (typeof direct === "string" && direct.trim()) return direct;
  const params = (res.action as { params?: Record<string, unknown> } | undefined)?.params;
  const p = params?.body ?? params?.text;
  return typeof p === "string" && p.trim() ? p : null;
}

