"""Audit hash-chain verification and the trust.audit_chain_break signal (03 spec 5.1)."""

from __future__ import annotations

from datetime import datetime, timezone

from clefs.audit import audit
from clefs.modules.trust import chain


def _rows(cur, slug, n):
    for i in range(n):
        audit(cur, None, "test.row", "thing", str(i), after={"i": i}, hotel_slug=slug)
    cur.execute("SELECT id FROM audit_log WHERE hotel_slug = %s ORDER BY id", (slug,))
    return [r["id"] for r in cur.fetchall()]


def _tamper(cur, sql, params):
    cur.execute("SET LOCAL clefs.audit_maintenance = 'on'")
    cur.execute(sql, params)
    cur.execute("SET LOCAL clefs.audit_maintenance = 'off'")


def _break_signals(cur, slug):
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND kind = %s", (slug, chain.SIGNAL_KIND))
    return cur.fetchall()


def test_clean_chain_verifies_incrementally(cur, hotel_uae):
    slug = hotel_uae.slug
    ids = _rows(cur, slug, 5)
    out = chain.verify_chain(cur, slug)
    assert out["status"] == "ok" and out["last_id"] == ids[-1] and out["rows_checked"] == len(ids)
    _rows(cur, slug, 2)
    again = chain.verify_chain(cur, slug)
    assert again["status"] == "ok" and again["rows_checked"] == 2
    assert chain.get_state(cur, slug)["status"] == "ok"
    assert not _break_signals(cur, slug)


def test_edited_row_raises_break_signal(cur, hotel_uae):
    slug = hotel_uae.slug
    ids = _rows(cur, slug, 4)
    _tamper(cur, "UPDATE audit_log SET after = '{\"i\": 99}' WHERE id = %s", (ids[2],))
    out = chain.verify_chain(cur, slug)
    assert out["status"] == "broken" and out["broken_at_id"] == ids[2]
    assert out["last_id"] == ids[1]  # the checkpoint stays on the last good row
    sigs = _break_signals(cur, slug)
    assert len(sigs) == 1 and sigs[0]["section"] == "compliance" and sigs[0]["subject_id"] == str(ids[2])
    # A second run re-reports the same break as the same signal.
    chain.verify_chain(cur, slug)
    assert len(_break_signals(cur, slug)) == 1


def test_deleted_row_breaks_the_link(cur, hotel_uae):
    slug = hotel_uae.slug
    ids = _rows(cur, slug, 4)
    _tamper(cur, "DELETE FROM audit_log WHERE id = %s", (ids[1],))
    out = chain.verify_chain(cur, slug)
    assert out["status"] == "broken" and out["broken_at_id"] == ids[2]
    assert "link" in out["reason"]


def test_edit_behind_checkpoint_is_caught_by_the_full_walk(cur, hotel_uae):
    slug = hotel_uae.slug
    ids = _rows(cur, slug, 3)
    assert chain.verify_chain(cur, slug)["status"] == "ok"
    _tamper(cur, "UPDATE audit_log SET entity_id = 'forged' WHERE id = %s", (ids[0],))
    assert chain.verify_chain(cur, slug)["status"] == "ok"  # incremental: nothing new
    assert chain.verify_chain(cur, slug, full=True)["status"] == "broken"


def test_anchor_after_retention_keeps_chain_valid(cur, hotel_uae):
    slug = hotel_uae.slug
    ids = _rows(cur, slug, 5)
    cur.execute("SELECT row_hash FROM audit_log WHERE id = %s", (ids[1],))
    anchor_hash = bytes(cur.fetchone()["row_hash"])
    _tamper(cur, "DELETE FROM audit_log WHERE hotel_slug = %s AND id <= %s", (slug, ids[1]))
    assert chain.verify_chain(cur, slug, full=True)["status"] == "broken"
    chain.set_anchor(cur, slug, anchor_id=ids[1], anchor_hash=anchor_hash)
    out = chain.verify_chain(cur, slug, full=True)
    assert out["status"] == "ok" and out["rows_checked"] == 3


def test_scheduled_job_runs_at_0330_dubai(cur, hotel_uae, clock, run_jobs):
    slug = hotel_uae.slug
    _rows(cur, slug, 2)
    clock.freeze(datetime(2026, 10, 5, 23, 31, tzinfo=timezone.utc))  # 03:31 Dubai
    run_jobs(["trust.audit_chain_verify"], scheduled=True)
    state = chain.get_state(cur, slug)
    assert state["status"] == "ok" and state["last_full_at"] is not None


def test_verify_route(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _rows(cur, slug, 2)
    c = client_as("gm")
    r = c.post(f"/hotels/{slug}/trust/audit/chain/verify")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert c.get(f"/hotels/{slug}/trust/audit/chain").json()["status"] == "ok"
    assert client_as("finance").post(f"/hotels/{slug}/trust/audit/chain/verify").status_code == 403
