"""clefs.notify.cancel_queued: retract a send that has not gone out yet (a
journey step whose stay was cancelled), with the outbox's own follow-ups.

Lives next to the core-domain tests because tests/foundation is frozen for
wave lanes; it only uses the conftest fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clefs import jobs, notify
from clefs.notify import outbox as ob

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)  # 12:00 Dubai
TO = "+971501234567"


def _queued(cur, slug, key, *, fallback_of=None, hours=2):
    row, _ = ob.create(
        cur, channel="whatsapp", kind="template", recipient=TO, payload={"to": TO[1:], "type": "template"},
        purpose="guests.journey.j2", hotel_slug=slug, idempotency_key=key, template_name="t", template_lang="en",
        not_before=T0 + timedelta(hours=hours), fallback_of=fallback_of,
    )
    return row


def _row(cur, oid):
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (oid,))
    return dict(cur.fetchone())


def _task_status(cur, oid):
    cur.execute("SELECT status FROM job_queue WHERE dedupe_key = %s", (f"{ob.DELIVER_TASK}:{oid}",))
    r = cur.fetchone()
    return r["status"] if r else None


def test_a_queued_send_is_cancelled_with_its_delivery_task(cur, hotel_uae, outbox, clock):
    clock.freeze(T0)
    row = _queued(cur, hotel_uae.slug, "gst:1:a1")
    assert _task_status(cur, row["id"]) == "queued"
    out = notify.cancel_queued(cur, idempotency_key="gst:1:a1", reason="stay_cancelled")
    assert out == {"outbox_id": row["id"], "status": "cancelled", "cancelled": True, "cancelled_ids": [row["id"]]}
    got = _row(cur, row["id"])
    assert got["meta"]["cancel_reason"] == "stay_cancelled" and got["meta"]["cancelled_at"]
    assert _task_status(cur, row["id"]) == "cancelled"
    # once due, nothing goes out: deliver leaves a non-queued row alone
    clock.advance(hours=3)
    assert ob.deliver(cur, row["id"])["status"] == "cancelled"
    assert notify.captured() == []
    # a second call is a no-op that still reports the row
    again = notify.cancel_queued(cur, outbox_id=row["id"])
    assert again["cancelled"] is False and again["status"] == "cancelled" and again["cancelled_ids"] == []


def test_lookup_by_id_key_and_hotel(cur, hotel_uae, outbox, clock):
    clock.freeze(T0)
    row = _queued(cur, hotel_uae.slug, "gst:2:a1")
    miss = {"outbox_id": None, "status": None, "cancelled": False, "cancelled_ids": []}
    assert notify.cancel_queued(cur, idempotency_key="nope") == miss
    assert notify.cancel_queued(cur, outbox_id=row["id"], hotel_slug="another-hotel") == miss
    # both given: both must match
    assert notify.cancel_queued(cur, outbox_id=row["id"], idempotency_key="gst:other") == miss
    assert _row(cur, row["id"])["status"] == "queued"
    out = notify.cancel_queued(cur, outbox_id=row["id"], idempotency_key="gst:2:a1", hotel_slug=hotel_uae.slug)
    assert out["cancelled"] is True
    with pytest.raises(ValueError):
        notify.cancel_queued(cur)


def test_a_sent_message_cannot_be_retracted(cur, hotel_uae, outbox, clock):
    clock.freeze(T0)
    r = notify.send_whatsapp_template(TO, "owner_brief_v1", "en", ["3", "AED 4,120"], purpose="brief.daily",
                                      hotel_slug=None, idempotency_key="brief:x", cur=cur)
    assert r.status == "sent"
    out = notify.cancel_queued(cur, idempotency_key="brief:x")
    assert out["cancelled"] is False and out["status"] == "sent" and out["outbox_id"] == r.outbox_id


def test_follow_ups_of_a_failed_first_attempt_are_cancelled(cur, hotel_uae, outbox, clock):
    """The first attempt failed and the outbox queued its own retry copy
    (fallback_of): retracting by the original key stops the re-send."""
    clock.freeze(T0)
    first = _queued(cur, hotel_uae.slug, "gst:3:a1")
    cur.execute("UPDATE outbound_messages SET status = 'failed' WHERE id = %s", (first["id"],))
    retry = _queued(cur, hotel_uae.slug, "gst:3:a1:retry:1", fallback_of=first["id"])
    fb = _queued(cur, hotel_uae.slug, "gst:3:a1:retry:1:fallback", fallback_of=retry["id"])
    out = notify.cancel_queued(cur, idempotency_key="gst:3:a1", followups=False)
    assert out["cancelled"] is False and out["status"] == "failed"
    assert _row(cur, retry["id"])["status"] == "queued"
    out = notify.cancel_queued(cur, idempotency_key="gst:3:a1")
    assert out["status"] == "failed" and out["cancelled"] is True
    assert out["cancelled_ids"] == [retry["id"], fb["id"]]
    assert {_row(cur, i)["status"] for i in (retry["id"], fb["id"])} == {"cancelled"}
    jobs.run_pending(only=[ob.DELIVER_TASK])
    assert notify.captured() == []
