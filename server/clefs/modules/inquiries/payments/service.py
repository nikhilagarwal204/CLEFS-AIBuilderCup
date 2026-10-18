"""
Payment-link flow (04 spec 4.8): provider accounts, link creation and
sending through `inquiries.send_payment_link`, the verified-webhook inbox,
status processing (only `fetch_status` changes a link), reconciliation,
fees, manual payments and the paid-not-in-PMS follow-up.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock, money, privacy
from clefs.core.errors import ClefsError, not_found
from clefs.core.hotels import Hotel
from clefs.db import Json
from clefs.modules.inquiries import store
from clefs.modules.inquiries.payments import DEFAULT_FEES, get_provider
from clefs.modules.inquiries.payments.base import (
    Account,
    PaymentEvent,
    PaymentLinkRequest,
    ProviderError,
    is_safe_pay_url,
    safe_pay_url,
)

PAY_KINDS = ("pay_ngenius", "pay_telr", "pay_stripe", "pay_tap")
OPEN_LINK_STATUSES = ("created", "sent")
PROCESS_TASK = "inquiries.payment_event_process"
PAID_NOT_IN_PMS_AFTER = timedelta(hours=48)

# ─── Accounts (inq_channel_accounts pay_*) ───


def _account(row: dict) -> Account:
    secret: dict = {}
    if row.get("secret_enc"):
        try:
            secret = json.loads(privacy.unseal(row["secret_enc"]) or "{}")
        except (ValueError, TypeError):
            secret = {}
    return Account(id=row["id"], hotel_slug=row["hotel_slug"], kind=row["kind"], config=row.get("config") or {},
                   secret=secret)


def connect(cur: Any, hotel_slug: str, *, provider: str, config: dict, secret: dict, actor: Any) -> dict:
    """Connect (or reconnect) a provider. The webhook key is new each time
    and shown once: only its sha256 is stored."""
    from clefs.audit import audit

    get_provider(provider)
    kind = f"pay_{provider}"
    if kind not in PAY_KINDS:
        raise ClefsError(400, "manual payments need no account", "invalid_provider")
    key = secrets.token_urlsafe(24)
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    clean = {k: v for k, v in (config or {}).items() if k in ("outlet_ref", "api_base", "webhook_header", "store_id",
                                                             "test_mode", "fee_pct", "fee_fixed_minor", "currency",
                                                             "post_url")}
    # api_base is fetched server-side, so it is N-Genius only and must be one
    # of its https gateways (SSRF: see ngenius.ALLOWED_HOST_SUFFIX).
    base = clean.pop("api_base", None)
    if provider == "ngenius":
        from clefs.modules.inquiries.payments.ngenius import clean_api_base

        if (base := clean_api_base(base)) is not None:
            clean["api_base"] = base
    cur.execute(
        """
        INSERT INTO inq_channel_accounts (hotel_slug, kind, config, secret_enc, webhook_key_hash, status, connected_by)
        VALUES (%s, %s, %s, %s, %s, 'active', %s)
        ON CONFLICT (hotel_slug, kind) DO UPDATE SET config = EXCLUDED.config, secret_enc = EXCLUDED.secret_enc,
          webhook_key_hash = EXCLUDED.webhook_key_hash, status = 'active', connected_by = EXCLUDED.connected_by,
          connected_at = now()
        RETURNING id, kind, config, status, connected_by, connected_at
        """,
        (hotel_slug, kind, Json(clean), privacy.seal(json.dumps(secret or {})), hashlib.sha256(key.encode()).digest(),
         user_id),
    )
    row = dict(cur.fetchone())
    audit(cur, actor, "inquiries.payment_connect", "inq_channel_account", row["id"], after={"kind": kind},
          hotel_slug=hotel_slug)
    return {**row, "webhook_key": key, "webhook_path": f"/webhooks/inquiries/payments/{provider}/{key}"}


def disconnect(cur: Any, hotel_slug: str, provider: str, *, actor: Any) -> bool:
    from clefs.audit import audit

    cur.execute(
        "UPDATE inq_channel_accounts SET status = 'disconnected', secret_enc = NULL, webhook_key_hash = NULL "
        "WHERE hotel_slug = %s AND kind = %s AND status <> 'disconnected' RETURNING id",
        (hotel_slug, f"pay_{provider}"),
    )
    row = cur.fetchone()
    if row:
        audit(cur, actor, "inquiries.payment_disconnect", "inq_channel_account", row["id"], hotel_slug=hotel_slug)
    return row is not None


def list_accounts(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        "SELECT id, kind, config, status, connected_by, connected_at FROM inq_channel_accounts "
        "WHERE hotel_slug = %s AND kind = ANY(%s) ORDER BY kind",
        (hotel_slug, list(PAY_KINDS)),
    )
    return [{**dict(r), "provider": r["kind"].removeprefix("pay_")} for r in cur.fetchall()]


def account_for(cur: Any, hotel_slug: str, provider: str | None = None) -> Account | None:
    """The named provider's active account, else the hotel's first active one."""
    kinds = [f"pay_{provider}"] if provider else list(PAY_KINDS)
    cur.execute(
        "SELECT * FROM inq_channel_accounts WHERE hotel_slug = %s AND kind = ANY(%s) AND status = 'active' "
        "ORDER BY array_position(%s::text[], kind) LIMIT 1",
        (hotel_slug, kinds, list(PAY_KINDS)),
    )
    row = cur.fetchone()
    return _account(dict(row)) if row else None


def account_by_key(cur: Any, provider: str, key: str) -> Account | None:
    if not key or len(key) > 200:
        return None
    cur.execute("SELECT * FROM inq_channel_accounts WHERE kind = %s AND webhook_key_hash = %s AND status = 'active'",
                (f"pay_{provider}", hashlib.sha256(key.encode()).digest()))
    row = cur.fetchone()
    return _account(dict(row)) if row else None


def account_by_id(cur: Any, account_id: int) -> Account | None:
    cur.execute("SELECT * FROM inq_channel_accounts WHERE id = %s", (account_id,))
    row = cur.fetchone()
    return _account(dict(row)) if row else None


# ─── Fees (4.8) ───


def settings_fee(provider: str, config: dict | None, amount_minor: int) -> int:
    """amount x fee_pct + fee_fixed_minor from the account config, else the
    provider's settings default."""
    pct_default, fixed_default = DEFAULT_FEES.get(provider, ("0", 0))
    cfg = config or {}
    pct = Decimal(str(cfg.get("fee_pct") if cfg.get("fee_pct") is not None else pct_default))
    fixed = int(cfg.get("fee_fixed_minor") if cfg.get("fee_fixed_minor") is not None else fixed_default)
    return int((Decimal(amount_minor) * pct / 100).quantize(Decimal(1), ROUND_HALF_UP)) + fixed


# ─── Requesting and sending a link ───

SEND_TYPE = "inquiries.send_payment_link"
PAY_TEXT = {
    "en": "Here is your secure payment link for {ref} ({label}, {amount}): {url}",
    "ar": "رابط الدفع الآمن لعرض السعر {ref} ({label}، {amount}): {url}",
}
PAY_SUBJECT = {"en": "Payment link for {ref}", "ar": "رابط الدفع لعرض السعر {ref}"}


def request_link(
    cur: Any, hotel: Hotel, quote: dict, *, purpose: str, amount_minor: int | None, actor: Any, via: str = "console",
    milestone: int | None = None, provider: str | None = None, url: str | None = None, approve_now: bool = False,
) -> dict:
    """Create the `inquiries.send_payment_link` action (the dial decides).
    `amount_minor` defaults to the milestone's amount (or the quote total for
    `full`). Returns the action."""
    from clefs.core import actions
    from clefs.modules.inquiries.quotes import money_label, verify_until_for

    if quote["status"] not in ("sent", "accepted"):
        raise ClefsError(409, "send the quote before a payment link", "quote_not_sent")
    if purpose not in ("deposit", "full", "balance"):
        raise ClefsError(400, "purpose must be deposit, full or balance", "invalid_purpose")
    sched = quote.get("deposit_schedule") or []
    ms = next((m for m in sched if m.get("seq") == milestone), None) if milestone else \
        next((m for m in sched if m.get("purpose") == purpose), None)
    amount = int(amount_minor if amount_minor is not None else
                 (ms or {}).get("amount_minor") or quote["total_minor"])
    if amount <= 0 or amount > quote["total_minor"]:
        raise ClefsError(400, "the amount must be positive and at most the quote total", "invalid_amount")
    if provider is None and url is None and account_for(cur, hotel.slug) is None:
        provider = "manual"
    if provider == "manual" and not url:
        raise ClefsError(409, "connect a payment provider or paste a link", "no_payment_provider")
    if url is not None:
        # A pasted link becomes the Pay button's href on the public quote
        # page: https only, so it can never be a javascript: or data: URL.
        url = safe_pay_url(url)
    inquiry = store.get_inquiry(cur, hotel.slug, quote["inquiry_id"])
    first_day = quote["lines"][0]["stay_date"]
    params = {"inquiry_id": inquiry["id"], "quote_id": quote["id"], "version": quote["version"], "purpose": purpose,
              "amount_minor": amount, "milestone": (ms or {}).get("seq") or milestone,
              "includes_td_minor": int((ms or {}).get("includes_td_minor") or 0), "provider": provider, "url": url,
              "segment": inquiry.get("segment"), "total_minor": quote["total_minor"],
              "rooms": sum(ln["rooms"] for ln in quote["lines"] if ln["stay_date"] == first_day),
              "nights": len({ln["stay_date"] for ln in quote["lines"]})}
    act = actions.create_action(
        cur, hotel.slug, type=SEND_TYPE, params=params, expected_minor=amount, currency=quote["currency"],
        title=f"Payment link {inquiry['ref'].upper()}-v{quote['version']}: {money_label(amount, quote['currency'])}",
        owner_role="revenue", dedupe_key=f"inq.link:{quote['id']}:{params['milestone'] or purpose}:{amount}",
        actor=actor, via=via, expires_at=quote["valid_until"] if quote["status"] == "sent" else None,
        verify_until=verify_until_for(hotel, inquiry),
    )
    if act.get("id") is None:
        raise ClefsError(409, "payment links are switched off for this hotel", "dial_off")
    if act["status"] == "proposed" and approve_now:
        act = actions.decide(cur, act["id"], "approve", actor=actor, via="console", hotel_slug=hotel.slug)
    return act


def _insert_link(cur: Any, hotel: Hotel, quote: dict, params: dict, *, provider: str, action_id: int | None) -> dict:
    key = f"{quote['id']}:{params.get('milestone') or params['purpose']}:{action_id}"
    cur.execute(
        """
        INSERT INTO inq_payment_links (hotel_slug, inquiry_id, quote_id, provider, amount_minor, currency, purpose,
                                       includes_td_minor, status, expires_at, idempotency_key, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'created', %s, %s, %s)
        ON CONFLICT (hotel_slug, idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
        RETURNING *
        """,
        (hotel.slug, quote["inquiry_id"], quote["id"], provider, int(params["amount_minor"]), quote["currency"],
         params["purpose"], int(params.get("includes_td_minor") or 0), link_expiry(hotel, quote), key, clock.now()),
    )
    return dict(cur.fetchone())


def link_expiry(hotel: Hotel, quote: dict) -> datetime:
    """A link lives while the quote is valid, and at least 24 h for an
    accepted quote (the guest accepted: give them a day to pay)."""
    now = clock.now()
    return max(quote["valid_until"], now + timedelta(hours=24)) if quote["status"] == "accepted" else quote["valid_until"]


def execute_send_link(ctx: Any) -> Any:
    """Executor of inquiries.send_payment_link: create the provider link,
    send it (free WhatsApp inside the window, else email; the quote page
    always shows it too), stage -> payment_pending."""
    import html as _html

    from clefs import notify
    from clefs.core.actions import ExecResult
    from clefs.modules.inquiries import quotes, service

    cur, hotel, p = ctx.cur, ctx.hotel, ctx.params
    q = quotes.quote_by_id(cur, int(p["quote_id"]))
    if q["status"] not in ("sent", "accepted"):
        return ExecResult(ok=False, error=f"quote_{q['status']}")
    inquiry = store.get_inquiry(cur, hotel.slug, q["inquiry_id"], lock=True)
    provider = p.get("provider") or ("manual" if p.get("url") else None)
    acct = None if provider == "manual" else account_for(cur, hotel.slug, provider)
    if acct is None and provider != "manual":
        return ExecResult(ok=False, error="no_payment_provider")
    provider = provider or acct.provider
    link = _insert_link(cur, hotel, q, p, provider=provider, action_id=ctx.action_id)
    if link["status"] == "created" and not link.get("url"):
        clear = store.unsealed(inquiry)
        ref = f"{inquiry['ref'].upper()}-v{q['version']}"
        req = PaymentLinkRequest(
            amount_minor=link["amount_minor"], currency=link["currency"], description=f"{hotel.name} {ref}",
            reference=f"{ref}-{p.get('milestone') or 1}", expires_at=link["expires_at"],
            customer_name=inquiry.get("contact_name"), customer_email=clear["email"], customer_phone=clear["phone"],
            language=q["language"], return_url=quotes.quote_url(cur, hotel.slug, quotes.public_token(q)),
            idempotency_key=link["idempotency_key"],
        )
        try:
            if provider == "manual":
                made = get_provider("manual").create_link(
                    Account(id=0, hotel_slug=hotel.slug, kind="manual", config={"url": p.get("url")}), req)
            else:
                made = get_provider(provider).create_link(acct, req)
        except ProviderError as exc:
            if exc.transient:
                raise  # the executor retries with backoff
            cur.execute("UPDATE inq_payment_links SET status = 'failed' WHERE id = %s", (link["id"],))
            return ExecResult(ok=False, error="provider_refused")
        cur.execute(
            "UPDATE inq_payment_links SET provider_ref = %s, url = %s, provider_payload = %s WHERE id = %s RETURNING *",
            (made.provider_ref, made.url, Json(json.loads(json.dumps(made.raw, default=str))), link["id"]),
        )
        link = dict(cur.fetchone())
    lang = "ar" if q["language"] == "ar" else "en"
    ref = f"{inquiry['ref'].upper()}-v{q['version']}"
    text = PAY_TEXT[lang].format(ref=ref, label=p["purpose"], url=link["url"],
                                 amount=quotes.money_label(link["amount_minor"], link["currency"]))
    clear = store.unsealed(inquiry)
    key = f"inq.link:{hotel.slug}:{link['id']}"
    sent = None
    ph = inquiry.get("contact_phone_hmac")
    if clear["phone"] and ph and notify.wa_window_open_until(cur, hotel.slug, sender_hmac=bytes(ph)):
        sent = notify.send_whatsapp_text(clear["phone"], text, hotel_slug=hotel.slug, purpose="inquiries.payment_link",
                                         sender="hotel", preview_url=True, idempotency_key=f"{key}:wa",
                                         action_id=ctx.action_id, cur=cur)
    if (sent is None or not sent.ok) and clear["email"]:
        sent = notify.send_email(clear["email"], PAY_SUBJECT[lang].format(ref=ref), f"<p>{_html.escape(text)}</p>",
                                 text=text, hotel_slug=hotel.slug, purpose="inquiries.payment_link", category="utility",
                                 idempotency_key=f"{key}:email", action_id=ctx.action_id, cur=cur)
    channel = sent.channel if sent is not None and sent.ok else "quote_page"
    cur.execute("UPDATE inq_payment_links SET status = 'sent' WHERE id = %s AND status = 'created'", (link["id"],))
    by = "staff" if ctx.action.get("decided_by") else "ai"
    store.add_message(cur, inquiry, direction="out", channel=channel if channel != "quote_page" else "web",
                      body=text, author_type=by, provider_message_id=f"paylink:{link['id']}",
                      outbound_message_id=getattr(sent, "outbox_id", None), action_id=ctx.action_id)
    service.on_outbound(cur, inquiry, by=by, hotel=hotel)
    if inquiry["stage"] in ("new", "replied"):
        service.transition(cur, inquiry, "quoted", reason="payment_link", hotel=hotel)
    if inquiry["stage"] == "quoted":
        service.transition(cur, inquiry, "payment_pending", reason="payment_link_sent", hotel=hotel,
                           data={"link_id": link["id"], "action_id": ctx.action_id})
    return ExecResult(ok=True, data={"link_id": link["id"], "channel": channel, "provider": provider},
                      consent_basis=f"inquiry:{inquiry['consent_basis']}",
                      reversal_handle={"kind": "payment_link", "link_id": link["id"]})


# ─── Webhook inbox (4.8 webhook route) ───

_SECRET_HEADERS = ("authorization", "stripe-signature", "hashstring", "x-webhook-secret", "x-telr-secret", "cookie")


def _redact_headers(headers: dict[str, str]) -> dict:
    return {k: ("[redacted]" if k.lower() in _SECRET_HEADERS or "secret" in k.lower() or "sign" in k.lower()
                else str(v)[:200]) for k, v in (headers or {}).items()}


def ingest_webhook(cur: Any, provider: str, account_key: str, headers: dict[str, str], raw: bytes) -> tuple[int, dict]:
    """1. Unknown account: 404, nothing stored. 2. Bad signature: stored in
    inq_payment_events_rejected, 401, no state change. 3. Verified events go
    to inq_payment_events (ON CONFLICT DO NOTHING; an id-less event is keyed
    on the body's sha256) and a processing task is queued. 4. 200 at once.
    Nothing here changes a link: the task asks the provider (fetch_status)."""
    from clefs import jobs

    if provider not in ("ngenius", "telr", "stripe", "tap"):
        return 404, {"detail": "unknown payment provider", "code": "unknown_provider"}
    acct = account_by_key(cur, provider, account_key)
    if acct is None:
        return 404, {"detail": "unknown payment account", "code": "unknown_account"}
    adapter = get_provider(provider)
    sha = hashlib.sha256(raw or b"").hexdigest()

    def reject(reason: str) -> None:
        cur.execute(
            "INSERT INTO inq_payment_events_rejected (hotel_slug, account_id, provider, reason, headers_redacted, "
            "payload_sha256) VALUES (%s, %s, %s, %s, %s, %s)",
            (acct.hotel_slug, acct.id, provider, reason, Json(_redact_headers(headers)), sha),
        )

    if not adapter.verify_webhook(acct, headers, raw or b""):
        reject("bad_signature")
        return 401, {"detail": "bad signature", "code": "bad_signature"}
    try:
        events = adapter.parse_webhook(acct, headers, raw or b"")
    except (ValueError, TypeError, KeyError):
        reject("unparseable")
        return 400, {"detail": "unparseable body", "code": "unparseable"}
    stored = 0
    for ev in events:
        eid = ev.provider_event_id or sha
        cur.execute("SELECT id FROM inq_payment_links WHERE provider = %s AND provider_ref = %s", (provider, ev.provider_ref))
        link = cur.fetchone()
        cur.execute(
            """
            INSERT INTO inq_payment_events (hotel_slug, account_id, provider, provider_event_id, payment_link_id, payload)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (provider, provider_event_id) DO NOTHING RETURNING id
            """,
            (acct.hotel_slug, acct.id, provider, eid, link["id"] if link else None,
             Json({"event": ev.model_dump(mode="json"), "body_sha256": sha})),
        )
        row = cur.fetchone()
        if row is None:
            continue  # a duplicate or a replay: already stored once
        stored += 1
        jobs.enqueue(PROCESS_TASK, {"event_id": row["id"]}, hotel_slug=acct.hotel_slug,
                     dedupe_key=f"inq.pay_event:{row['id']}", cur=cur)
    return 200, {"ok": True, "stored": stored}


def process_event(cur: Any, hotel: Hotel, event_id: int) -> dict:
    """The task behind every stored event: ask the provider (fetch_status,
    authoritative) and apply that answer. The webhook body is never used."""
    cur.execute("SELECT * FROM inq_payment_events WHERE id = %s AND hotel_slug = %s FOR UPDATE", (event_id, hotel.slug))
    ev = cur.fetchone()
    if ev is None or ev["processed_at"] is not None:
        return {"skipped": True}
    ev = dict(ev)
    ref = ((ev["payload"] or {}).get("event") or {}).get("provider_ref")
    cur.execute("SELECT * FROM inq_payment_links WHERE hotel_slug = %s AND ((id = %s) OR (provider = %s AND provider_ref = %s)) "
                "ORDER BY id LIMIT 1 FOR UPDATE", (hotel.slug, ev.get("payment_link_id"), ev["provider"], ref))
    link = cur.fetchone()
    changed = False
    if link is not None:
        acct = account_by_id(cur, ev["account_id"])
        status = get_provider(ev["provider"]).fetch_status(acct, link["provider_ref"]) if acct else None
        if status is not None:
            changed = apply_status(cur, hotel, dict(link), status, config=acct.config if acct else {})
    cur.execute("UPDATE inq_payment_events SET processed_at = %s, payment_link_id = COALESCE(payment_link_id, %s) "
                "WHERE id = %s", (clock.now(), link["id"] if link else None, event_id))
    return {"changed": changed, "link_id": link["id"] if link else None}


# ─── Applying a status (only from fetch_status or a manual mark) ───

_NEXT: dict[str, tuple[str, ...]] = {
    "created": ("sent", "paid", "partially_paid", "expired", "cancelled", "failed"),
    "sent": ("paid", "partially_paid", "expired", "cancelled", "failed"),
    "partially_paid": ("paid", "refunded"),
    "paid": ("refunded",),
    "failed": ("paid", "partially_paid"),
    "expired": ("paid", "partially_paid"),  # a late payment still counts
    "cancelled": ("paid", "partially_paid"),
    "refunded": (),
}


def apply_status(cur: Any, hotel: Hotel, link: dict, ev: PaymentEvent, *, config: dict | None) -> bool:
    """Move the link to what the provider says. Paid (or partly paid): the
    fee is the provider's when it reports one, else settings; the inquiry
    moves to payment_pending if it was earlier and verification is queued.
    Refunded: the verification is reversed. Returns whether anything moved."""
    from clefs import jobs

    to = ev.kind
    if to == "paid" and ev.amount_minor and ev.amount_minor < link["amount_minor"]:
        to = "partially_paid"
    if to == link["status"] or to not in _NEXT.get(link["status"], ()):
        return False
    changes: dict[str, Any] = {"status": to}
    if to in ("paid", "partially_paid"):
        paid = int(ev.amount_minor or link["amount_minor"])
        fee, source = (int(ev.fee_minor), "provider") if ev.fee_minor is not None else (
            settings_fee(link["provider"], config, paid), "settings")
        changes.update(paid_at=ev.occurred_at or clock.now(), paid_amount_minor=paid, fee_minor=fee, fee_source=source)
    sets = ", ".join(f"{k} = %s" for k in changes)
    cur.execute(f"UPDATE inq_payment_links SET {sets} WHERE id = %s", (*changes.values(), link["id"]))
    if to in ("paid", "partially_paid"):
        from clefs.modules.inquiries import service

        inquiry = store.get_inquiry(cur, hotel.slug, link["inquiry_id"], lock=True)
        if inquiry["stage"] == "quoted":
            service.transition(cur, inquiry, "payment_pending", reason=f"payment_{to}", hotel=hotel,
                               data={"link_id": link["id"]})
        jobs.enqueue("inquiries.verify_inquiry", {"inquiry_id": link["inquiry_id"]}, hotel_slug=hotel.slug,
                     dedupe_key=f"inq.verify:{link['inquiry_id']}:{link['id']}:{to}", cur=cur)
    elif to == "refunded":
        from clefs.modules.inquiries import verify

        verify.on_refund(cur, hotel, link)
    return True


# ─── Reconciliation (hourly) and expiry ───


def reconcile(cur: Any, hotel: Hotel) -> dict:
    """Poll every open link (the webhook may never come), then expire the
    lapsed ones. An inquiry left in payment_pending with nothing open or
    paid is lost as PAYMENT_LINK_EXPIRED (4.10)."""
    now = clock.now()
    cur.execute("SELECT * FROM inq_payment_links WHERE hotel_slug = %s AND status = ANY(%s) ORDER BY id FOR UPDATE SKIP LOCKED",
                (hotel.slug, list(OPEN_LINK_STATUSES)))
    polled = moved = expired = 0
    for link in [dict(r) for r in cur.fetchall()]:
        acct = None if link["provider"] == "manual" else account_for(cur, hotel.slug, link["provider"])
        if acct is not None and link.get("provider_ref"):
            polled += 1
            try:
                st = get_provider(link["provider"]).fetch_status(acct, link["provider_ref"])
            except ProviderError:
                st = None
            if st is not None and apply_status(cur, hotel, link, st, config=acct.config):
                moved += 1
                continue
        if link.get("expires_at") and link["expires_at"] <= now:
            if acct is not None and link.get("provider_ref"):
                try:
                    get_provider(link["provider"]).cancel_link(acct, link["provider_ref"])
                except ProviderError:
                    pass
            cur.execute("UPDATE inq_payment_links SET status = 'expired' WHERE id = %s", (link["id"],))
            expired += 1
            _lost_if_nothing_left(cur, hotel, link["inquiry_id"])
    return {"polled": polled, "moved": moved, "expired": expired}


def _lost_if_nothing_left(cur: Any, hotel: Hotel, inquiry_id: int) -> None:
    from clefs.modules.inquiries import service

    cur.execute("SELECT 1 FROM inq_payment_links WHERE inquiry_id = %s AND status = ANY(%s)",
                (inquiry_id, ["created", "sent", "paid", "partially_paid"]))
    if cur.fetchone() is not None:
        return
    inquiry = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
    if inquiry["stage"] == "payment_pending":
        service.transition(cur, inquiry, "lost", reason="auto:payment_link_expired", lost_reason="PAYMENT_LINK_EXPIRED",
                           hotel=hotel)


# ─── Staff actions on a link ───


def get_link(cur: Any, hotel_slug: str, link_id: int, *, lock: bool = False) -> dict:
    cur.execute(f"SELECT * FROM inq_payment_links WHERE hotel_slug = %s AND id = %s{' FOR UPDATE' if lock else ''}",
                (hotel_slug, link_id))
    row = cur.fetchone()
    if row is None:
        raise not_found("payment link not found")
    return dict(row)


def mark_paid_manual(cur: Any, hotel: Hotel, link_id: int, *, evidence_file_id: str, actor: Any) -> dict:
    """A bank transfer or a pasted link paid outside any integration. Only
    the manual provider: an integrated link is paid when its provider says so."""
    from clefs import files
    from clefs.audit import audit

    link = get_link(cur, hotel.slug, link_id, lock=True)
    if link["provider"] != "manual":
        raise ClefsError(409, "only a manual payment can be marked paid by hand", "not_manual")
    try:
        files.file_row(cur, hotel.slug, evidence_file_id)
    except ClefsError as exc:
        raise ClefsError(400, "attach the transfer evidence first", "evidence_required") from exc
    ev = PaymentEvent(provider="manual", provider_event_id=f"manual:{link_id}", provider_ref=link["provider_ref"] or "",
                      kind="paid", amount_minor=link["amount_minor"], currency=link["currency"], occurred_at=clock.now(),
                      fee_minor=0)
    if not apply_status(cur, hotel, link, ev, config={}):
        raise ClefsError(409, f"this link is {link['status']}", "illegal_link_state")
    cur.execute("UPDATE inq_payment_links SET provider_payload = COALESCE(provider_payload, '{}'::jsonb) || %s "
                "WHERE id = %s RETURNING *", (Json({"evidence_file_id": str(evidence_file_id)}), link_id))
    out = dict(cur.fetchone())
    audit(cur, actor, "inquiries.payment_mark_paid", "inq_payment_link", link_id,
          after={"amount_minor": link["amount_minor"], "evidence_file_id": str(evidence_file_id)}, hotel_slug=hotel.slug)
    return out


def cancel(cur: Any, hotel: Hotel, link_id: int, *, actor: Any) -> dict:
    from clefs.audit import audit

    link = get_link(cur, hotel.slug, link_id, lock=True)
    if link["status"] not in OPEN_LINK_STATUSES:
        raise ClefsError(409, f"this link is {link['status']}", "illegal_link_state")
    acct = None if link["provider"] == "manual" else account_for(cur, hotel.slug, link["provider"])
    if acct is not None and link.get("provider_ref"):
        try:
            get_provider(link["provider"]).cancel_link(acct, link["provider_ref"])
        except ProviderError:
            pass  # our row is what the quote page reads; the provider link lapses anyway
    cur.execute("UPDATE inq_payment_links SET status = 'cancelled' WHERE id = %s RETURNING *", (link_id,))
    out = dict(cur.fetchone())
    audit(cur, actor, "inquiries.payment_cancel", "inq_payment_link", link_id, hotel_slug=hotel.slug)
    return out


def list_links(cur: Any, inquiry_id: int) -> list[dict]:
    cur.execute("SELECT * FROM inq_payment_links WHERE inquiry_id = %s ORDER BY id DESC", (inquiry_id,))
    return [dict(r) for r in cur.fetchall()]


def link_to_api(link: dict) -> dict:
    out = {k: v for k, v in link.items() if k != "provider_payload"}
    ccy = link["currency"]
    for k in ("amount_minor", "includes_td_minor", "paid_amount_minor", "fee_minor"):
        out[k.replace("_minor", "_value")] = money.money_str(link.get(k), ccy)
    out["evidence_file_id"] = (link.get("provider_payload") or {}).get("evidence_file_id")
    return out


def open_link_url(cur: Any, token: str) -> str | None:
    """The link the quote page shows: the newest sent, unpaid link of the
    quote (the page is where a guest outside the WhatsApp window pays)."""
    from clefs.modules.inquiries import quotes

    try:
        q = quotes.by_token(cur, token)
    except ClefsError:
        return None
    cur.execute("SELECT url FROM inq_payment_links WHERE quote_id = %s AND status = 'sent' AND url IS NOT NULL "
                "AND (expires_at IS NULL OR expires_at > %s) ORDER BY id DESC LIMIT 1", (q["id"], clock.now()))
    row = cur.fetchone()
    # Defence in depth: a row written before the https check (or an odd
    # provider response) is never handed to the page as an href.
    return row["url"] if row and is_safe_pay_url(row["url"]) else None
