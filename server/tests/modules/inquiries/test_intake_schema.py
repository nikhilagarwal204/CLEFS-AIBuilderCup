"""Inquiry Guard schema smoke test: the 4.1 tables exist after migrations."""

from __future__ import annotations


def test_inquiry_tables_exist(cur):
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'inq\\_%%' ORDER BY table_name"
    )
    names = {r["table_name"] for r in cur.fetchall()}
    assert {
        "inq_inquiries", "inq_messages", "inq_stage_events", "inq_stage_probabilities", "inq_templates",
        "inq_quotes", "inq_channel_accounts", "inq_payment_links", "inq_payment_events",
        "inq_payment_events_rejected", "inq_outcomes", "inq_match_reviews",
    } <= names
