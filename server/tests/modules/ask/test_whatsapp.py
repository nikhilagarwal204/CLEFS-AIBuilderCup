"""WhatsApp Q&A through the real webhook dispatch: opted-in owner/gm/finance
only, the reply shape (text, aligned rows, magic link), refusals, the hotel
prefix, and the per-answer magic session."""

from __future__ import annotations

import itertools

import pytest

from clefs import notify
from clefs.modules.ask import store, whatsapp  # noqa: F401 - registers the handler (as the app does)

from .conftest import call, grounded_answer, q

PLATFORM = "PNID-PLATFORM"
OWNER_PHONE = "+971501234567"
_ids = itertools.count(1)


@pytest.fixture
def wa(app, monkeypatch, outbox):
    """`app` registers the module permissions (ask.view), as the API process does."""
    from clefs.settings import reload_settings

    monkeypatch.setenv("WA_PLATFORM_PHONE_NUMBER_ID", PLATFORM)
    monkeypatch.setenv("NOTIFY_MODE", "capture")
    reload_settings()
    yield outbox
    monkeypatch.undo()
    reload_settings()


def send(text: str, sender: str = OWNER_PHONE) -> dict:
    from clefs.core import clock

    m = {"from": sender.lstrip("+"), "id": f"wamid.ask.{next(_ids)}", "timestamp": str(int(clock.now().timestamp())),
         "type": "text", "text": {"body": text}}
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PLATFORM}, "messages": [m]}}]}]}
    return notify.process_whatsapp(body)


def replies() -> list[str]:
    from clefs import db

    rows = db.fetch_all("SELECT payload FROM outbound_messages WHERE purpose = 'ask.whatsapp_answer' ORDER BY id")
    return [r["payload"]["text"]["body"] for r in rows]


@pytest.fixture
def owner_wa(cur, seeded, make_user, hotel_uae, wa):
    u = make_user("owner", hotel_uae.slug)
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", (OWNER_PHONE, u["id"]))
    store.set_optin(cur, hotel_uae.slug, u["id"], opted_in=True, method="console")
    return u


def test_opted_in_owner_gets_text_rows_and_link(cur, owner_wa, fake_ai):
    fake_ai.push([call("query_metric", **q("pg.commission_aed", dims=["channel"], preset="last_month"))],
                 grounded_answer)
    send("Commission by channel last month?")
    body = replies()[-1]
    assert body.startswith("OTA commission paid for 1-31 Aug 2026 was 14,700.00")
    assert "```\nBOOKING  AED 10,200.00\nEXPEDIA  AED 4,500.00\nDIRECT   AED 0.00\n```" in body
    assert "Open in Clefs: " in body and "/auth/magic/" in body
    cur.execute("SELECT channel, status, user_id FROM ask_logs ORDER BY id DESC LIMIT 1")
    log = cur.fetchone()
    assert log["channel"] == "WHATSAPP" and log["status"] == "answered" and log["user_id"] == owner_wa["id"]


def test_not_opted_in_or_wrong_role_is_not_answered(cur, seeded, make_user, hotel_uae, wa, fake_ai):
    u = make_user("owner", hotel_uae.slug)
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", (OWNER_PHONE, u["id"]))
    send("Commission last month")
    assert replies() == [] and fake_ai.calls == []
    head = make_user("dept_head", hotel_uae.slug)
    cur.execute("UPDATE users SET phone_e164 = '+971509990000' WHERE id = %s", (head["id"],))
    store.set_optin(cur, hotel_uae.slug, head["id"], opted_in=True, method="admin")
    send("Commission last month", sender="+971509990000")
    assert replies() == [] and fake_ai.calls == []


def test_pii_question_is_refused_without_a_model_call(owner_wa, fake_ai):
    send("What is guest John's phone number?")
    body = replies()[-1]
    assert "never shows a guest's personal details" in body and "/auth/magic/" not in body
    assert fake_ai.calls == []


def test_stop_is_left_to_the_brief(owner_wa, fake_ai):
    send("STOP")
    assert replies() == [] and fake_ai.calls == []


def test_hotel_prefix_picks_the_hotel(cur, owner_wa, fake_ai):
    from clefs.core import hotels

    hotels.create_hotel(cur, slug="test-abudhabi", name="Test Hotel Abu Dhabi", rooms_count=10)
    cur.execute("INSERT INTO hotel_memberships (user_id, hotel_slug, role, is_active) VALUES (%s, %s, 'owner', TRUE)",
                (owner_wa["id"], "test-abudhabi"))
    store.set_optin(cur, "test-abudhabi", owner_wa["id"], opted_in=True, method="console")
    fake_ai.push([call("answer_unsupported", reason="No data yet.", suggestions=[])])
    send("test-abudhabi: commission last month")
    cur.execute("SELECT hotel_slug, question FROM ask_logs ORDER BY id DESC LIMIT 1")
    log = cur.fetchone()
    assert log["hotel_slug"] == "test-abudhabi" and log["question"] == "commission last month"
    assert replies()[-1].startswith("(Test Hotel Abu Dhabi) No data yet.")


def test_magic_link_opens_exactly_that_answer(cur, app, owner_wa, fake_ai):
    from fastapi.testclient import TestClient

    fake_ai.push([call("query_metric", **q("pg.commission_aed", preset="last_month"))], grounded_answer)
    send("Commission last month")
    token = replies()[-1].rsplit("/auth/magic/", 1)[1].strip()
    cur.execute("SELECT id FROM ask_logs ORDER BY id DESC LIMIT 1")
    log_id = cur.fetchone()["id"]
    c = TestClient(app)
    r = c.post("/auth/magic/exchange", json={"token": token})
    assert r.status_code == 200 and r.json()["next_path"] == f"/app/test-dubai/ask?log={log_id}"
    assert c.get(f"/hotels/test-dubai/ask/logs/{log_id}").status_code == 200
    other = store.insert_log(cur, "test-dubai", user_id=owner_wa["id"], channel="WEB", question="x", tool_calls=[],
                             answer={}, status="answered", reason=None, grounded=None, regenerated=False,
                             latency_ms=1, tokens=None)
    assert c.get(f"/hotels/test-dubai/ask/logs/{other}").status_code == 403
    assert c.post("/hotels/test-dubai/ask", json={"question": "x"}).status_code == 403  # magic: no ask.view
