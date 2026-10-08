"""
Money (Canon 2), consent (PDPL: separate purposes, append-only, no consent
by default) and the privacy primitives (per-hotel contact HMAC, AES-GCM
sealing, the PII registry).
"""

from __future__ import annotations

import base64
import importlib.util
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from clefs.core import consent, money, privacy
from clefs.core.errors import ClefsError, NotConfigured


# ─── Money ───


def test_decimal_helpers_and_minor_units():
    assert money.D(0.1) == Decimal("0.1") and money.D("1,234.50") == Decimal("1234.50")
    with pytest.raises(ValueError):
        money.D(None)
    assert money.quantize("2.345", "AED") == Decimal("2.35")  # half up, never banker's rounding
    assert money.quantize("2.3449", "AED") == Decimal("2.34")
    assert money.quantize("-2.345", "AED") == Decimal("-2.35")
    assert money.to_minor(Decimal("12.345"), "AED") == 1235
    assert money.to_minor("1.2345", "OMR") == 1235 and money.to_minor("1500.4", "JPY") == 1500
    assert money.from_minor(123450, "AED") == Decimal("1234.50")
    assert money.money_str(84700, "AED") == "847.00" and money.money_str(None, "AED") is None
    assert money.money_str(1235, "KWD") == "1.235"
    with pytest.raises(ClefsError):
        money.ccy("dirham")


def test_formatting():
    assert money.fmt_money(Decimal("1234.5"), "AED") == "AED 1,234.50"
    assert money.fmt_money(-12, "aed") == "-AED 12.00"
    assert money.fmt_money(12345678, "INR", "en-IN") == "INR 1,23,45,678.00"
    assert money.fmt_money(102000, "AED", minor=True) == "AED 1,020.00"
    assert money.fmt_money(5, "JPY") == "JPY 5"


def test_allocate_always_adds_up():
    parts = money.allocate(10000, [1, 1, 1])
    assert sum(parts) == 10000 and parts == [3334, 3333, 3333]
    assert sum(money.allocate(49000, ["396.49", "39.65", "30.53", "23.33"])) == 49000
    assert money.allocate(-7, [1, 1]) == [-3, -4]
    with pytest.raises(ValueError):
        money.allocate(1, [0, 0])


def test_fx_uses_the_peg_without_a_rates_table(cur):
    on = date(2026, 10, 1)
    assert money.convert(cur, Decimal("100"), "USD", "AED", on) == Decimal("367.25")
    assert money.convert(cur, Decimal("367.25"), "AED", "USD", on) == Decimal("100.00")
    assert money.convert(cur, Decimal("5"), "AED", "aed", on) == Decimal("5.00")
    assert money.convert_minor(cur, 10000, "USD", "AED", on) == 36725
    with pytest.raises(ClefsError) as exc:
        money.convert(cur, Decimal("1"), "EUR", "AED", on)
    assert exc.value.code == "fx_unavailable"
    cur.execute("SELECT 1 AS alive")  # a probe of a missing table never poisons the transaction
    assert cur.fetchone()["alive"] == 1


def test_fx_prefers_the_rates_table_when_present(cur):
    # core/tax owns fx_rates (0020-0099); until it lands, create it inside the
    # test transaction (rolled back afterwards) with the 02 spec's columns.
    cur.execute("SELECT to_regclass('public.fx_rates') IS NOT NULL AS present")
    if not cur.fetchone()["present"]:
        cur.execute(
            "CREATE TABLE public.fx_rates (date DATE, base CHAR(3), quote CHAR(3), rate NUMERIC(18, 8), source TEXT, "
            "PRIMARY KEY (date, base, quote))"
        )
    cur.execute("INSERT INTO fx_rates (date, base, quote, rate, source) VALUES ('2026-09-30', 'EUR', 'AED', 4.25, 'MANUAL')")
    on = date(2026, 10, 1)
    assert money.convert(cur, Decimal("10"), "EUR", "AED", on) == Decimal("42.50")
    assert money.convert(cur, Decimal("42.50"), "AED", "EUR", on) == Decimal("10.00")
    with pytest.raises(ClefsError):
        money.convert(cur, Decimal("10"), "EUR", "AED", date(2026, 9, 29))  # no rate on or before that day
    assert money.convert(cur, Decimal("1"), "USD", "AED", on) == Decimal("3.67")  # still the peg for USD


@pytest.mark.skipif(importlib.util.find_spec("clefs.core.tax") is not None, reason="core/tax is installed")
def test_tax_composition_is_delegated_to_core_tax():
    with pytest.raises(NotConfigured):
        money.decompose(Decimal("500"), 1, {})


# ─── Consent ───


def test_consent_is_off_by_default_and_append_only(cur, hotel_uae, owner_ctx, clock):
    clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    ref = privacy.contact_ref(hotel_uae.slug, "phone", "+971 50 123 4567")
    assert not consent.has_consent(cur, hotel_uae.slug, ref, "marketing")
    consent.record_consent(cur, hotel_uae.slug, ref, "marketing", True, source="registration_card", evidence={"file_id": "f1"}, actor=owner_ctx)
    consent.record_consent(cur, hotel_uae.slug, ref, "transfer_abroad", True, source="registration_card")
    assert consent.has_consent(cur, hotel_uae.slug, ref, "marketing")
    assert not consent.has_consent(cur, hotel_uae.slug, ref, "recording")  # purposes are separate
    clock.advance(minutes=1)
    consent.withdraw(cur, hotel_uae.slug, ref, "marketing", source="whatsapp_stop")
    assert not consent.has_consent(cur, hotel_uae.slug, ref, "marketing")
    cur.execute("SELECT granted FROM consents WHERE subject_ref = %s AND purpose = 'marketing' ORDER BY id", (ref,))
    assert [r["granted"] for r in cur.fetchall()] == [True, False]
    state = consent.consent_state(cur, hotel_uae.slug, ref)
    assert state["marketing"]["granted"] is False and state["transfer_abroad"]["granted"] is True
    cur.execute("SELECT action FROM audit_log WHERE entity_type = 'consent' ORDER BY id")
    assert [r["action"] for r in cur.fetchall()] == ["core.consent.grant", "core.consent.grant", "core.consent.withdraw"]


def test_consent_expiry_and_validation(cur, hotel_uae, clock):
    now = clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    consent.record_consent(cur, hotel_uae.slug, b"\x01" * 32, "recording", True, source="concierge", expires_at=now + timedelta(days=1))
    assert consent.has_consent(cur, hotel_uae.slug, (b"\x01" * 32).hex(), "recording")
    clock.advance(days=2)
    assert not consent.has_consent(cur, hotel_uae.slug, b"\x01" * 32, "recording")
    for bad in (
        lambda: consent.record_consent(cur, hotel_uae.slug, "r", "telepathy", True, source="x"),
        lambda: consent.record_consent(cur, hotel_uae.slug, "r", "marketing", True, source="x", subject_type="alien"),
        lambda: consent.record_consent(cur, hotel_uae.slug, "", "marketing", True, source="x"),
        lambda: consent.record_consent(cur, hotel_uae.slug, "r", "marketing", True, source=""),
    ):
        with pytest.raises(ClefsError):
            bad()
    consent.register_consent_purpose("review_request")
    assert "review_request" in consent.purposes()
    consent.record_consent(cur, hotel_uae.slug, "r", "review_request", True, source="x")


def test_consent_texts_are_versioned_and_immutable(cur, hotel_uae):
    t = consent.record_consent_text(cur, "marketing", "2026-10-01", "May we send you offers?")
    assert consent.record_consent_text(cur, "marketing", "2026-10-01", "May we send you offers?")["id"] == t["id"]
    with pytest.raises(ClefsError) as exc:
        consent.record_consent_text(cur, "marketing", "2026-10-01", "Different words")
    assert exc.value.code == "consent_text_immutable"
    own = consent.record_consent_text(cur, "marketing", "h1", "Hotel wording", hotel_slug=hotel_uae.slug)
    assert consent.current_consent_text(cur, "marketing", hotel_slug=hotel_uae.slug)["id"] == own["id"]
    assert consent.current_consent_text(cur, "marketing")["id"] == t["id"]
    row = consent.record_consent(cur, hotel_uae.slug, "r1", "marketing", True, source="web_form", text_version="2026-10-01")
    assert row["text_id"] == t["id"]
    with pytest.raises(ClefsError) as exc:
        consent.record_consent(cur, hotel_uae.slug, "r1", "marketing", True, source="web_form", text_version="nope")
    assert exc.value.code == "unknown_consent_text"


# ─── Privacy ───


def test_contact_hmac_is_stable_per_hotel_and_normalized():
    a = privacy.contact_hmac("demo-dubai", "phone", "+971 50-123 4567")
    assert a == privacy.contact_hmac("demo-dubai", "phone", "00971501234567") and len(a) == 32
    assert a == privacy.contact_hmac("demo-dubai", "phone", "050 123 4567", default_country_code="971")
    assert a != privacy.contact_hmac("other-hotel", "phone", "+971501234567")  # no cross-hotel joins
    assert privacy.contact_hmac("h", "email", " Sam@Example.COM ") == privacy.contact_hmac("h", "email", "sam@example.com")
    with pytest.raises(ClefsError):
        privacy.contact_hmac("h", "email", "not-an-email")
    with pytest.raises(ClefsError):
        privacy.contact_hmac("h", "phone", "12345")


def test_pepper_changes_the_hmac(env):
    a = privacy.contact_hmac("h", "phone", "+971501234567")
    env(CONTACT_HMAC_PEPPER="another-pepper")
    assert privacy.contact_hmac("h", "phone", "+971501234567") != a


def test_seal_roundtrip_and_tamper_detection(env):
    blob = privacy.seal("+971501234567")
    assert blob[0] == privacy.SEAL_VERSION and b"971" not in blob
    assert privacy.unseal(blob) == "+971501234567" and privacy.unseal(None) is None
    assert privacy.seal("x") != privacy.seal("x")  # a fresh nonce every time
    tampered = bytearray(blob)
    tampered[-1] ^= 1
    with pytest.raises(ValueError):
        privacy.unseal(bytes(tampered))
    key = bytes(range(32))
    env(PII_ENC_KEY=base64.b64encode(key).decode())
    sealed = privacy.seal("secret")
    env(PII_ENC_KEY=key.hex())
    assert privacy.unseal(sealed) == "secret"  # hex and base64 name the same key
    env(PII_ENC_KEY="too-short")
    with pytest.raises(NotConfigured):
        privacy.seal("x")


def test_production_refuses_derived_keys(env):
    env(ENV="production", SECRET_KEY="p" * 40, CONTACT_HMAC_PEPPER=None, PII_ENC_KEY=None)
    with pytest.raises(NotConfigured):
        privacy.contact_hmac("h", "phone", "+971501234567")
    with pytest.raises(NotConfigured):
        privacy.seal("x")


def test_pii_registry():
    spec = privacy.register_pii("guests", "gst_test_profiles", subject_columns={"phone_hmac": "hmac"}, ts_column="updated_at",
                                retention_days=730, strategy="redact", redact_columns=("name",), where="provenance <> 'pms_direct'")
    assert spec in privacy.pii_registry()
    assert {("core", "inbound_messages"), ("core", "outbound_messages")} <= {(s.module, s.table) for s in privacy.pii_registry()}
    for bad in (
        dict(strategy="shred"),
        dict(strategy="redact", redact_columns=()),
        dict(retention_days=0),
        dict(ts_column="updated_at; DROP TABLE x"),
        dict(where="1=1; DELETE FROM hotels"),
    ):
        kw = dict(subject_columns={"a": "phone"}, ts_column="t", retention_days=1, strategy="delete")
        kw.update(bad)
        with pytest.raises(ValueError):
            privacy.register_pii("guests", "gst_bad", **kw)
    assert privacy.mask_phone("+971501234567") == "+971••••••567" and privacy.mask_email("sam@example.com") == "s•••@example.com"
    # National format keeps only the last 3 digits too (was 8 of 10).
    assert privacy.mask_phone("0501234567") == "•••••••567"
