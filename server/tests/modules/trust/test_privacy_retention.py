"""Retention policies and the nightly purge (03 spec 5.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clefs.core import clock as core_clock
from clefs.core import privacy
from clefs.modules.trust import chain, retention


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _old_and_new_logs(cur, slug, now):
    for age in (400, 10):
        cur.execute(
            "INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp) "
            "VALUES (%s, '101', 's1', 'user', %s, %s)",
            (slug, f"age {age}", _ms(now - timedelta(days=age))),
        )
        cur.execute(
            "INSERT INTO staff_requests (hotel_slug, room, type, description, timestamp) VALUES (%s, '101', 'Cleaning', %s, %s)",
            (slug, f"my wife is allergic, age {age}", _ms(now - timedelta(days=age))),
        )


def _log_rows(cur, slug):
    cur.execute("SELECT data_class, table_name, action, row_count, criteria FROM trs_deletion_log WHERE hotel_slug = %s", (slug,))
    return {r["data_class"]: dict(r) for r in cur.fetchall()}


def test_purge_deletes_and_anonymizes_old_rows_only(cur, hotel_uae):
    slug, now = hotel_uae.slug, core_clock.now()
    _old_and_new_logs(cur, slug, now)
    out = retention.run_purge(slug, only=["transcripts", "request_free_text"])
    assert out["failures"] == [] and out["counts"] == {"transcripts": 1, "request_free_text": 1}
    cur.execute("SELECT message FROM conversation_logs WHERE hotel_slug = %s", (slug,))
    assert [r["message"] for r in cur.fetchall()] == ["age 10"]
    cur.execute("SELECT description FROM staff_requests WHERE hotel_slug = %s ORDER BY timestamp", (slug,))
    assert [r["description"] for r in cur.fetchall()] == ["[anonymized]", "my wife is allergic, age 10"]
    log = _log_rows(cur, slug)
    assert log["transcripts"]["row_count"] == 1 and log["request_free_text"]["action"] == "anonymize"
    # Counts only: nothing from the rows themselves reaches the log.
    assert "allergic" not in str(log) and "age 400" not in str(log)
    # A second run finds nothing new (anonymized rows are not re-touched).
    again = retention.run_purge(slug, only=["transcripts", "request_free_text"])
    assert again["counts"] == {"transcripts": 0, "request_free_text": 0}


def _guest_word_columns(cur):
    # Service adds these in 0100_service_requests_v2.sql; a trust-only test
    # run has no service migrations, so add them here (rolled back per test).
    cur.execute("ALTER TABLE staff_requests ADD COLUMN IF NOT EXISTS original_text TEXT, "
                "ADD COLUMN IF NOT EXISTS guest_comment TEXT")


def test_purge_anonymizes_original_text_and_guest_comment(cur, hotel_uae):
    """Regression (spec-erase-retention-leave-guest-words): the 365-day purge
    must take the guest's own-language wording and their tracker comment, not
    only the English description."""
    slug, now = hotel_uae.slug, core_clock.now()
    _guest_word_columns(cur)
    old = _ms(now - timedelta(days=400))
    rows = (
        ("my wife is allergic", "زوجتي لديها حساسية", "still not fixed"),
        ("[erased]", "[erased]", None),  # erased by a DSR: its marker must stay
        ("towels please", None, None),  # NULL cells stay NULL and do not re-trigger
    )
    for desc, orig, comment in rows:
        cur.execute(
            "INSERT INTO staff_requests (hotel_slug, room, type, description, original_text, guest_comment, timestamp) "
            "VALUES (%s, '101', 'Cleaning', %s, %s, %s, %s)",
            (slug, desc, orig, comment, old),
        )
    out = retention.run_purge(slug, only=["request_free_text"])
    assert out["failures"] == [] and out["counts"] == {"request_free_text": 2}
    cur.execute("SELECT description, original_text, guest_comment FROM staff_requests "
                "WHERE hotel_slug = %s ORDER BY id", (slug,))
    got = [(r["description"], r["original_text"], r["guest_comment"]) for r in cur.fetchall()]
    assert got == [
        ("[anonymized]", "[anonymized]", "[anonymized]"),
        ("[erased]", "[erased]", None),
        ("[anonymized]", None, None),
    ]
    assert "حساسية" not in str(got) and "not fixed" not in str(got)
    again = retention.run_purge(slug, only=["request_free_text"])
    assert again["counts"] == {"request_free_text": 0}


def test_purge_still_works_without_the_service_columns(cur, hotel_uae):
    # The CASE values reference only their own column, so a schema without
    # original_text / guest_comment still anonymizes the description.
    slug, now = hotel_uae.slug, core_clock.now()
    cur.execute("ALTER TABLE staff_requests DROP COLUMN IF EXISTS original_text, DROP COLUMN IF EXISTS guest_comment")
    _old_and_new_logs(cur, slug, now)
    out = retention.run_purge(slug, only=["request_free_text"])
    assert out["failures"] == [] and out["counts"] == {"request_free_text": 1}


def test_other_hotels_rows_are_untouched(cur, hotel_uae, legacy_slug):
    now = core_clock.now()
    _old_and_new_logs(cur, legacy_slug, now)
    retention.run_purge(hotel_uae.slug, only=["transcripts"])
    cur.execute("SELECT count(*) AS n FROM conversation_logs WHERE hotel_slug = %s AND message = 'age 400'", (legacy_slug,))
    assert cur.fetchone()["n"] == 1


def test_hotel_policy_changes_the_cutoff(cur, hotel_uae, client_as):
    slug, now = hotel_uae.slug, core_clock.now()
    _old_and_new_logs(cur, slug, now)
    owner = client_as("owner")
    r = owner.put(f"/hotels/{slug}/trust/retention/transcripts", json={"keep_days": 5})
    assert r.status_code == 200 and r.json()["keep_days"] == 5 and r.json()["overridden"] is True
    assert retention.run_purge(slug, only=["transcripts"])["counts"]["transcripts"] == 2
    assert owner.put(f"/hotels/{slug}/trust/retention/transcripts", json={"keep_days": None}).json()["keep_days"] == 365


def test_bounds_and_permissions(hotel_uae, client_as):
    slug = hotel_uae.slug
    owner = client_as("owner")
    r = owner.put(f"/hotels/{slug}/trust/retention/audit_log", json={"keep_days": 30})
    assert r.status_code == 400 and r.json()["code"] == "keep_days_out_of_bounds"
    assert owner.put(f"/hotels/{slug}/trust/retention/nope", json={"keep_days": 30}).status_code == 404
    assert client_as("gm").put(f"/hotels/{slug}/trust/retention/transcripts", json={"keep_days": 30}).status_code == 403
    listing = client_as("gm").get(f"/hotels/{slug}/trust/retention").json()
    by = {p["data_class"]: p for p in listing}
    assert by["audit_log"]["keep_days"] == 730 and by["audit_log"]["min_keep_days"] == 365
    assert by["transcripts"]["keep_days"] == 365 and by["deletion_log"]["keep_days"] == 1095
    assert by["core.inbound_messages"]["source"] == "registry"


def test_registry_table_is_redacted_by_column_type(cur, hotel_uae, monkeypatch):
    slug, now = hotel_uae.slug, core_clock.now()
    cur.execute(
        "CREATE TABLE trs_test_notes (id SERIAL PRIMARY KEY, hotel_slug TEXT, created_at TIMESTAMPTZ NOT NULL, "
        "note TEXT NOT NULL, body JSONB NOT NULL, phone TEXT)"
    )
    spec = privacy.PiiSpec("trust", "trs_test_notes", {"phone": "phone"}, "created_at", 30, "redact", ("note", "body", "phone"))
    monkeypatch.setitem(privacy._registry, ("trust", "trs_test_notes"), spec)
    for age in (40, 1):
        cur.execute(
            "INSERT INTO trs_test_notes (hotel_slug, created_at, note, body, phone) VALUES (%s, %s, 'secret', '{\"a\": 1}', '+971501234567')",
            (slug, now - timedelta(days=age)),
        )
    out = retention.run_purge(slug, only=["trust.trs_test_notes"])
    assert out["counts"] == {"trust.trs_test_notes": 1}
    cur.execute("SELECT note, body, phone FROM trs_test_notes ORDER BY created_at")
    rows = cur.fetchall()
    assert dict(rows[0]) == {"note": "[anonymized]", "body": {}, "phone": None}
    assert rows[1]["note"] == "secret"
    assert retention.run_purge(slug, only=["trust.trs_test_notes"])["counts"] == {"trust.trs_test_notes": 0}


def _audit_at(cur, slug, at, action="old.row"):
    cur.execute(
        "INSERT INTO audit_log (hotel_slug, at, actor_type, action) VALUES (%s, %s, 'system', %s) RETURNING id",
        (slug, at, action),
    )
    return cur.fetchone()["id"]


def test_audit_purge_keeps_chain_verifiable(cur, hotel_uae):
    slug, now = hotel_uae.slug, core_clock.now()
    old = [_audit_at(cur, slug, now - timedelta(days=800 - i)) for i in range(3)]
    new = [_audit_at(cur, slug, now - timedelta(days=5), "new.row") for _ in range(2)]
    out = retention.run_purge(slug, only=["audit_log"])
    assert out["counts"]["audit_log"] == 3 and out["failures"] == []
    cur.execute("SELECT id FROM audit_log WHERE hotel_slug = %s ORDER BY id", (slug,))
    remaining = [r["id"] for r in cur.fetchall()]
    assert not set(old) & set(remaining) and set(new) <= set(remaining)
    assert chain.verify_chain(cur, slug, full=True)["status"] == "ok"
    assert chain.get_state(cur, slug)["anchor_id"] == old[-1]


def test_broken_chain_holds_audit_purge_and_raises_signal(cur, hotel_uae):
    slug, now = hotel_uae.slug, core_clock.now()
    ids = [_audit_at(cur, slug, now - timedelta(days=800)) for _ in range(2)]
    cur.execute("SET LOCAL clefs.audit_maintenance = 'on'")
    cur.execute("UPDATE audit_log SET action = 'forged' WHERE id = %s", (ids[0],))
    cur.execute("SET LOCAL clefs.audit_maintenance = 'off'")
    out = retention.run_purge(slug, only=["audit_log"])
    assert out["failures"] == [{"data_class": "audit_log", "error": "audit_chain_broken"}]
    cur.execute("SELECT count(*) AS n FROM audit_log WHERE id = ANY(%s)", (ids,))
    assert cur.fetchone()["n"] == 2
    cur.execute("SELECT status, section FROM signals WHERE hotel_slug = %s AND kind = %s", (slug, retention.SIGNAL_FAILED))
    sig = cur.fetchone()
    assert sig is not None and sig["section"] == "compliance"


@pytest.mark.parametrize("hhmm_utc", [(22, 31)])  # 02:31 Dubai
def test_nightly_job_runs_at_0230_dubai(cur, hotel_uae, clock, run_jobs, hhmm_utc):
    clock.freeze(datetime(2026, 10, 5, *hhmm_utc, tzinfo=timezone.utc))
    _old_and_new_logs(cur, hotel_uae.slug, core_clock.now())
    run_jobs(["trust.retention_purge"], scheduled=True)
    cur.execute("SELECT count(*) AS n FROM conversation_logs WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 1
    assert "transcripts" in _log_rows(cur, hotel_uae.slug)


def test_run_now_route_is_owner_only_and_audited(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _old_and_new_logs(cur, slug, core_clock.now())
    assert client_as("gm").post(f"/hotels/{slug}/trust/retention/run").status_code == 403
    r = client_as("owner").post(f"/hotels/{slug}/trust/retention/run")
    assert r.status_code == 200 and r.json()["counts"]["transcripts"] == 1
    log = client_as("gm").get(f"/hotels/{slug}/trust/retention/log").json()
    assert any(x["data_class"] == "transcripts" and x["row_count"] == 1 for x in log)
    cur.execute("SELECT count(*) AS n FROM audit_log WHERE hotel_slug = %s AND action = 'trust.retention.run'", (slug,))
    assert cur.fetchone()["n"] == 1


def test_every_data_class_purges_cleanly(cur, hotel_uae):
    """Each class (built-in and every PII registrant loaded in this process)
    produces valid SQL against its real table: nothing fails, nothing is
    signalled. Run with all modules enabled, this covers every lane's entry."""
    out = retention.run_purge(hotel_uae.slug)
    assert out["failures"] == [], out["failures"]
    assert set(out["counts"]) == set(retention.catalogue())
    platform = retention.run_platform_purge()
    assert set(platform["counts"]) == {k for k, dc in retention.catalogue().items() if dc.source == "registry"}
    cur.execute("SELECT count(*) AS n FROM signals WHERE hotel_slug = %s AND kind = %s", (hotel_uae.slug, retention.SIGNAL_FAILED))
    assert cur.fetchone()["n"] == 0
