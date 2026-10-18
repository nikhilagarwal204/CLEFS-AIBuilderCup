"""
Web Push for floor devices (03 spec 1.6): subscription storage and a send
that stays log-only until VAPID keys are configured.

- The device registers /device-sw.js, subscribes with the public VAPID key
  (NEXT_PUBLIC_VAPID_PUBLIC_KEY on the client) and POSTs the subscription to
  /device/service/push-subscriptions after the PIN login (a user gesture).
- A subscription belongs to the paired device and, when a PIN session is
  open, to the person who enabled it. `endpoint` is globally unique: the same
  browser re-subscribing updates its row.
- send_to_devices() is best effort and never raises. WHY log-only by
  default: the device already polls its feed and loops its alarm in the
  foreground; push only wakes a backgrounded Android tab. Real delivery
  needs VAPID_PRIVATE_KEY + VAPID_SUBJECT in the environment and the
  optional `pywebpush` package (imported inside try/except ImportError, so
  requirements.txt does not change until the foundation adds it).
- Payloads carry ids and a short title only, never the guest's words: the
  push service (Google FCM, Mozilla) is a third party.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError

log = logging.getLogger("clefs.service.push")

MAX_PER_DEVICE = 5  # live subscriptions kept per device; older ones are revoked
FAILURES_TO_REVOKE = 5
KEYS = ("id", "device_id", "staff_member_id", "endpoint", "created_at", "updated_at", "last_sent_at", "revoked_at")

# ─── Storage ───


def _clean(sub: dict) -> tuple[str, str, str, Any]:
    """(endpoint, p256dh, auth, expiration) from PushSubscription.toJSON()."""
    endpoint = str((sub or {}).get("endpoint") or "").strip()
    keys = (sub or {}).get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or "").strip(), str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or len(endpoint) > 2000:
        raise ClefsError(422, "subscription.endpoint must be an https URL", "invalid_subscription")
    if not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100:
        raise ClefsError(422, "subscription.keys needs p256dh and auth", "invalid_subscription")
    exp = sub.get("expirationTime")
    expiration = None
    if isinstance(exp, (int, float)) and exp > 0:
        from datetime import datetime, timezone

        expiration = datetime.fromtimestamp(exp / 1000, tz=timezone.utc)
    return endpoint, p256dh, auth, expiration


def out(r: dict) -> dict:
    o = {k: r.get(k) for k in KEYS}
    for k in ("created_at", "updated_at", "last_sent_at", "revoked_at"):
        o[k] = o[k].isoformat() if o[k] else None
    return o


def save(cur: Any, hotel_slug: str, device_id: int, sub: dict, *, staff_id: int | None,
         user_agent: str | None = None) -> tuple[dict, bool]:
    """Upsert one subscription. -> (row, created). A re-subscribe of the same
    endpoint clears its revocation and failure count and moves it to this
    device and person. Only the newest MAX_PER_DEVICE stay live per device
    (a PWA reinstalled daily must not fan one alarm out ten times)."""
    endpoint, p256dh, auth, expiration = _clean(sub)
    now = clock.now()
    cur.execute(
        """
        INSERT INTO svc_push_subscriptions (hotel_slug, device_id, staff_member_id, endpoint, p256dh, auth,
            expiration_at, user_agent, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (endpoint) DO UPDATE SET hotel_slug = EXCLUDED.hotel_slug, device_id = EXCLUDED.device_id,
            staff_member_id = EXCLUDED.staff_member_id, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
            expiration_at = EXCLUDED.expiration_at, user_agent = EXCLUDED.user_agent, updated_at = EXCLUDED.updated_at,
            failure_count = 0, revoked_at = NULL
        RETURNING *, (xmax = 0) AS created
        """,
        (hotel_slug, device_id, staff_id, endpoint, p256dh, auth, expiration, (user_agent or "")[:300] or None, now, now),
    )
    row = dict(cur.fetchone())
    cur.execute(
        """
        UPDATE svc_push_subscriptions SET revoked_at = %s
        WHERE device_id = %s AND revoked_at IS NULL AND id NOT IN (
            SELECT id FROM svc_push_subscriptions WHERE device_id = %s AND revoked_at IS NULL
            ORDER BY updated_at DESC, id DESC LIMIT %s)
        """,
        (now, device_id, device_id, MAX_PER_DEVICE),
    )
    return row, bool(row.pop("created"))


def revoke(cur: Any, hotel_slug: str, device_id: int, endpoint: str | None = None) -> int:
    """Revoke this device's subscription for `endpoint` (or all of its
    subscriptions when None). -> rows revoked."""
    sql = "UPDATE svc_push_subscriptions SET revoked_at = %s WHERE hotel_slug = %s AND device_id = %s AND revoked_at IS NULL"
    params: list[Any] = [clock.now(), hotel_slug, device_id]
    if endpoint:
        sql += " AND endpoint = %s"
        params.append(endpoint.strip())
    cur.execute(sql, params)
    return cur.rowcount


def live_for(cur: Any, hotel_slug: str, device_ids: list[int] | None = None) -> list[dict]:
    """Live subscriptions of the hotel (or of `device_ids`) on unrevoked devices."""
    sql = """SELECT s.* FROM svc_push_subscriptions s JOIN devices d ON d.id = s.device_id
             WHERE s.hotel_slug = %s AND s.revoked_at IS NULL AND d.revoked_at IS NULL
               AND (s.expiration_at IS NULL OR s.expiration_at > %s)"""
    params: list[Any] = [hotel_slug, clock.now()]
    if device_ids is not None:
        sql += " AND s.device_id = ANY(%s)"
        params.append(list(device_ids))
    cur.execute(sql + " ORDER BY s.id", params)
    return [dict(r) for r in cur.fetchall()]


# ─── Send (best effort, log-only without VAPID) ───


def _vapid() -> tuple[str, str] | None:
    """(private key, subject) from the environment, or None. WHY os.environ
    and not clefs.settings: the foundation has no VAPID fields yet (a
    FOUNDATION-REQUEST); the lookup moves there without changing callers."""
    key = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    subject = (os.environ.get("VAPID_SUBJECT") or "").strip()
    return (key, subject) if key and subject else None


def _webpush() -> Any:
    try:
        from pywebpush import webpush  # optional: not in requirements.txt yet
    except ImportError:
        return None
    return webpush


def mode() -> str:
    """live (keys + library + NOTIFY_MODE=live) or log (everything else)."""
    from clefs.settings import get_settings

    if get_settings().NOTIFY_MODE != "live" or _vapid() is None or _webpush() is None:
        return "log"
    return "live"


def send_to_devices(cur: Any, hotel_slug: str, *, title: str, body: str, tag: str,
                    device_ids: list[int] | None = None, url: str | None = None, urgent: bool = False) -> dict:
    """Push one short notice to the hotel's devices (or `device_ids`).
    Never raises, and runs in a SAVEPOINT: in Postgres one failed statement
    aborts the caller's whole transaction (an SOS must still be filed).
    -> {subscriptions, sent, failed, revoked, mode}."""
    res = {"subscriptions": 0, "sent": 0, "failed": 0, "revoked": 0, "mode": "log"}
    cur.execute("SAVEPOINT svc_push")
    try:
        _send(cur, hotel_slug, title, body, tag, device_ids, url, urgent, res)
    except Exception:  # noqa: BLE001 - a push is an accelerator, never a blocker
        cur.execute("ROLLBACK TO SAVEPOINT svc_push")
        log.exception("push to %s failed", hotel_slug)
        return res
    cur.execute("RELEASE SAVEPOINT svc_push")
    return res


def _send(cur: Any, hotel_slug: str, title: str, body: str, tag: str, device_ids: list[int] | None, url: str | None,
          urgent: bool, res: dict) -> None:
    subs = live_for(cur, hotel_slug, device_ids)
    res["subscriptions"] = len(subs)
    res["mode"] = m = mode()
    payload = json.dumps({"title": title[:80], "body": body[:200], "tag": tag[:64], "url": url or "/device",
                          "urgent": bool(urgent)})
    if not subs:
        return
    if m == "log":
        log.info("push (log mode) %s -> %d subscription(s): %s", hotel_slug, len(subs), payload)
        return
    key, subject = _vapid() or ("", "")
    webpush = _webpush()
    for s in subs:
        _deliver_one(cur, s, webpush, payload, key, subject, urgent, res)


def _deliver_one(cur: Any, s: dict, webpush: Any, payload: str, key: str, subject: str, urgent: bool, res: dict) -> None:
    now = clock.now()
    try:
        webpush(subscription_info={"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                data=payload, vapid_private_key=key, vapid_claims={"sub": subject}, ttl=300 if urgent else 3600,
                headers={"Urgency": "high" if urgent else "normal"}, timeout=5)
    except Exception as exc:  # noqa: BLE001 - WebPushException or a network fault
        status = getattr(getattr(exc, "response", None), "status_code", None)
        gone = status in (404, 410)  # the browser dropped the subscription
        fails = int(s.get("failure_count") or 0) + 1
        revoke_now = gone or fails >= FAILURES_TO_REVOKE
        cur.execute("UPDATE svc_push_subscriptions SET failure_count = %s, revoked_at = %s WHERE id = %s",
                    (fails, now if revoke_now else None, s["id"]))
        res["failed"] += 1
        res["revoked"] += int(revoke_now)
        log.warning("push to subscription %s failed (%s): %s", s["id"], status, exc)
        return
    cur.execute("UPDATE svc_push_subscriptions SET last_sent_at = %s, failure_count = 0 WHERE id = %s", (now, s["id"]))
    res["sent"] += 1
