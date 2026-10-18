"""Guest identity (03 spec 2.7, 04 spec 3.4): consent-gated profiles, own
contact capture, evidence rules and the profile_memory wording."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.core import consent, privacy
from clefs.core.errors import ClefsError
from clefs.modules.guests import api, contacts, identity, stays_port

PHONE = "+971501234567"
EMAIL = "guest@example.com"
TRANSFER = {"transfer_abroad": True}


# ─── Helpers (imported by the other test_identity_* files) ───


def versions(cur, slug, *purposes, language="en"):
    return {p: identity.text_version(cur, slug, p, language) for p in purposes}


def channel(cur, slug, code, kind="other"):
    cur.execute(
        "INSERT INTO channels (hotel_slug, code, name, kind) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (slug, code, code.title(), kind),
    )


def make_res(cur, slug, *, code="DIRECT_WEB", check_in=date(2026, 10, 1), check_out=date(2026, 10, 4),
             status="CONFIRMED", room="101", pms="R1"):
    channel(cur, slug, code)
    cur.execute(
        """
        INSERT INTO reservations (hotel_slug, pms_res_id, channel_code, check_in, check_out, nights, status, currency,
                                  room_number, guest_language)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'AED', %s, 'en') RETURNING id
        """,
        (slug, pms, code, check_in, check_out, (check_out - check_in).days, status, room),
    )
    return cur.fetchone()["id"]


def add_contact(cur, slug, res_id, value, *, kind="phone", provenance="pms_direct"):
    h = privacy.contact_hmac(slug, kind, value)
    cur.execute(
        """
        INSERT INTO reservation_contacts (hotel_slug, reservation_id, kind, value_enc, value_hmac, provenance)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (slug, res_id, kind, privacy.seal(value), h, provenance),
    )
    return h


def capture(cur, slug, *, value=PHONE, kind="phone", source="console", consents=None, evidence=None, **kw):
    consents = {**TRANSFER, **(consents or {})}
    return api.record_own_contact(
        cur, slug, kind=kind, value=value, capture_source=source, consents=consents,
        consent_text_versions=versions(cur, slug, *[p for p, g in consents.items() if g]),
        evidence=evidence if evidence is not None else {}, actor=None, **kw,
    )


def grant(cur, slug, h, *purposes):
    for p in purposes:
        consent.record_consent(cur, slug, h.hex(), p, True, source="test", subject_type="contact",
                               text_version=identity.text_version(cur, slug, p))


# ─── Consent wording ───


def test_profile_memory_wording_discloses_health_data(cur, hotel_uae):
    # Pinned: another lane's newer platform wording (Trust seeds one) must
    # not replace the only one that discloses health data.
    for lang in ("en", "ar"):
        assert identity.text_version(cur, hotel_uae.slug, "profile_memory", lang) == "gst.v2"
    en = identity.consent_text(cur, hotel_uae.slug, "profile_memory", "en")
    assert "allergies" in en["body"] and "health information" in en["body"]
    ar = identity.consent_text(cur, hotel_uae.slug, "profile_memory", "ar")
    assert ar["language"] == "ar" and "صحية" in ar["body"]
    # a hotel's own wording still wins
    consent.record_consent_text(cur, "profile_memory", "hotel-1", "Our own words about {hotel}.", hotel_slug=hotel_uae.slug)
    assert identity.text_version(cur, hotel_uae.slug, "profile_memory", "en") == "hotel-1"
    cur.execute("SELECT retired_at FROM consent_texts WHERE purpose = 'profile_memory' AND version = 'gst.v1'")
    rows = cur.fetchall()
    assert len(rows) == 2 and all(r["retired_at"] is not None for r in rows)


def test_direct_channels_include_corporate():
    assert stays_port.is_direct("CORPORATE") and not stays_port.is_ota("CORPORATE")


# ─── Profiles are consent-gated ───


def test_no_profile_without_profile_memory_and_transfer_consent(cur, hotel_uae):
    s = hotel_uae.slug
    h = identity.hmac_for(cur, s, "phone", PHONE)
    assert identity.upsert_profile(cur, s, phone_hmac=h) is None
    grant(cur, s, h, "profile_memory")
    assert identity.upsert_profile(cur, s, phone_hmac=h) is None  # transfer_abroad missing
    grant(cur, s, h, "transfer_abroad")
    row = identity.upsert_profile(cur, s, phone_hmac=h, first_name="Aisha",
                                  preferences={"tags": ["extra pillows"], "health": {"allergies": ["nuts"]}})
    assert row is not None and bytes(row["phone_hmac"]) == h
    assert privacy.unseal(row["first_name_enc"]) == "Aisha"
    cur.execute("SELECT COUNT(*) AS n FROM gst_profiles WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 1


def test_capture_merges_preferences_and_forget_deletes_the_profile(cur, hotel_uae):
    s = hotel_uae.slug
    capture(cur, s, consents={"profile_memory": True}, evidence={"preferences": {"tags": ["quiet room"]}})
    capture(cur, s, consents={"profile_memory": True},
            evidence={"preferences": {"tags": ["extra pillows"], "health": {"allergies": ["Nuts"]}}})
    h = identity.hmac_for(cur, s, "phone", PHONE)
    prof = identity.find_profile(cur, s, [h])
    assert prof["preferences"]["tags"] == ["quiet room", "extra pillows"]
    assert prof["preferences"]["health"]["allergies"] == ["Nuts"]
    # evidence stored on the point never carries the profile fields
    cur.execute("SELECT evidence FROM gst_contact_points WHERE hotel_slug = %s", (s,))
    assert all("preferences" not in (r["evidence"].get("capture") or {}) for r in cur.fetchall())
    assert identity.forget_profile(cur, s, prof["id"], source="test")
    assert identity.find_profile(cur, s, [h]) is None
    assert not consent.has_consent(cur, s, h.hex(), "profile_memory")


def test_phone_and_email_captured_together_make_one_profile(cur, hotel_uae):
    from clefs.modules.guests import linking

    s = hotel_uae.slug
    out = linking.capture_room_identity(
        cur, s, "101", phone=PHONE, email=EMAIL, consents={"transfer_abroad": True, "profile_memory": True},
        first_name="Omar",
    )
    cur.execute("SELECT id, phone_hmac, email_hmac FROM gst_profiles WHERE hotel_slug = %s", (s,))
    rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["phone_hmac"] and rows[0]["email_hmac"]
    assert out["profile_id"] == rows[0]["id"]
    cur.execute("SELECT guest_profile_id, contact_hmacs FROM gst_stays WHERE id = %s", (out["stay_id"],))
    stay = cur.fetchone()
    assert stay["guest_profile_id"] == rows[0]["id"] and len(stay["contact_hmacs"]) == 2


# ─── record_own_contact rules (04 spec 3.4) ───


@pytest.mark.parametrize("source", contacts.OWN_CAPTURE_SOURCES)
def test_transfer_consent_required_for_every_capture_source(cur, hotel_uae, source):
    kw = dict(kind="phone", value=PHONE, capture_source=source, evidence={}, actor=None)
    with pytest.raises(api.ConsentNotAllowed) as exc:
        api.record_own_contact(cur, hotel_uae.slug, consents={"marketing_whatsapp": True},
                               consent_text_versions=versions(cur, hotel_uae.slug, "marketing_whatsapp"), **kw)
    assert exc.value.reason == "transfer_abroad_required" and exc.value.status == 422
    with pytest.raises(api.ConsentNotAllowed):  # ticked but no wording version
        api.record_own_contact(cur, hotel_uae.slug, consents=TRANSFER, consent_text_versions={}, **kw)
    cur.execute("SELECT COUNT(*) AS n FROM gst_contact_points WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 0


def test_every_granted_consent_needs_its_wording_version(cur, hotel_uae):
    s = hotel_uae.slug
    with pytest.raises(api.ConsentNotAllowed) as exc:
        api.record_own_contact(cur, s, kind="phone", value=PHONE, capture_source="concierge",
                               consents={**TRANSFER, "marketing_whatsapp": True},
                               consent_text_versions=versions(cur, s, "transfer_abroad"), evidence={}, actor=None)
    assert exc.value.reason == "text_version_required"


def test_console_capture_stores_a_sealed_guest_direct_point_with_consents(cur, hotel_uae):
    s = hotel_uae.slug
    pid = capture(cur, s, consents={"marketing_whatsapp": True, "whatsapp_updates": True})
    cur.execute("SELECT * FROM gst_contact_points WHERE id = %s", (pid,))
    p = cur.fetchone()
    h = identity.hmac_for(cur, s, "phone", PHONE)
    assert p["provenance"] == "guest_direct" and bytes(p["value_hmac"]) == h and p["country_prefix"] == "+971"
    assert privacy.unseal(p["value_enc"]) == PHONE and PHONE.encode() not in bytes(p["value_enc"])
    state = consent.consent_state(cur, s, h.hex())
    assert state["marketing_whatsapp"]["granted"] and state["marketing_whatsapp"]["text_version"] == "gst.v1"
    assert state["transfer_abroad"]["granted"]


def test_j1_form_on_an_ota_booking_is_a_stay_only_contact(cur, hotel_uae):
    s = hotel_uae.slug
    rid = make_res(cur, s, code="BOOKING", check_out=date(2026, 10, 4))
    add_contact(cur, s, rid, "guest123@guest.booking.com", kind="email", provenance="ota_alias")
    for bad in ({"marketing_whatsapp": True}, {"marketing_email": True}, {"profile_memory": True}):
        with pytest.raises(api.ConsentNotAllowed) as exc:
            capture(cur, s, source="j1_form", reservation_id=rid, consents=bad)
        assert exc.value.reason == "ota_j1_form"
    pid = capture(cur, s, source="j1_form", reservation_id=rid, consents={"whatsapp_updates": True})
    cur.execute("SELECT provenance, usable_until, reservation_id FROM gst_contact_points WHERE id = %s", (pid,))
    p = cur.fetchone()
    assert p["provenance"] == "guest_stay" and p["reservation_id"] == rid
    # check_out + 30 d, end of that day in Dubai
    assert p["usable_until"] == datetime(2026, 11, 3, 19, 59, tzinfo=timezone.utc)
    # a guest_stay point never reaches marketing
    assert api.marketing_audience(cur, s) == []


def test_j1_form_on_a_direct_booking_may_opt_into_marketing(cur, hotel_uae):
    s = hotel_uae.slug
    rid = make_res(cur, s, code="DIRECT_WEB")
    pid = capture(cur, s, source="j1_form", reservation_id=rid, consents={"marketing_whatsapp": True})
    cur.execute("SELECT provenance FROM gst_contact_points WHERE id = %s", (pid,))
    assert cur.fetchone()["provenance"] == "guest_direct"


# ─── Registration-card evidence ───


def _file(cur, slug, data, ctype):
    from clefs import files

    return files.put_file(cur, slug, data, filename="card", content_type=ctype, purpose="guests.contact_evidence").id


def test_registration_card_evidence_rules(cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    with pytest.raises(ClefsError) as exc:
        capture(cur, s, source="registration_card", evidence={})
    assert exc.value.code == "evidence_required"
    big = _file(cur, s, b"\x89PNG" + b"0" * (300 * 1024), "image/png")
    with pytest.raises(ClefsError) as exc:
        capture(cur, s, source="registration_card", evidence={"kind": "card_crop", "file_id": big})
    assert exc.value.code == "evidence_too_large"
    mrz = _file(cur, s, b"P<GBRSMITH<<JOHN<<<<<<<<<<<<<<<<<<<<<<<<<<<\n", "text/plain")
    with pytest.raises(ClefsError) as exc:
        capture(cur, s, source="registration_card", evidence={"kind": "card_crop", "file_id": mrz})
    assert exc.value.code == "evidence_has_id"
    img = _file(cur, s, b"\x89PNG small crop", "image/png")
    fake_ai.push({"has_mrz": True, "has_id_document": False})
    with pytest.raises(ClefsError) as exc:
        capture(cur, s, source="registration_card", evidence={"kind": "card_crop", "file_id": img})
    assert exc.value.code == "evidence_has_id"
    fake_ai.push({"has_mrz": False, "has_id_document": False})
    pid = capture(cur, s, source="registration_card", evidence={"kind": "card_crop", "file_id": img})
    assert pid > 0
    with pytest.raises(ClefsError) as exc:
        capture(cur, s, source="registration_card", evidence={"kind": "staff_attestation"})
    assert exc.value.code == "evidence_required"


def test_forget_contact_deletes_evidence_and_keeps_the_stop(cur, hotel_uae):
    s = hotel_uae.slug
    sig = _file(cur, s, b"\x89PNG signature", "image/png")
    capture(cur, s, source="registration_card", consents={"marketing_whatsapp": True, "profile_memory": True},
            evidence={"kind": "staff_attestation", "signature_file_id": sig})
    api.opt_out(cur, s, kind="phone", value=PHONE, source="whatsapp")
    out = api.forget_contact(cur, s, kind="phone", value=PHONE, source="dsr")
    assert out == {"points_purged": 1, "profiles_deleted": 1, "evidence_files_deleted": 1}
    cur.execute("SELECT status, value_hmac, value_enc FROM gst_contact_points WHERE hotel_slug = %s", (s,))
    p = cur.fetchone()
    assert p["status"] == "purged" and p["value_hmac"] is None and p["value_enc"] is None
    cur.execute("SELECT deleted_at FROM files WHERE id = %s", (sig,))
    assert cur.fetchone()["deleted_at"] is not None
    h = identity.hmac_for(cur, s, "phone", PHONE)
    assert contacts.is_suppressed(cur, s, [h], marketing=True)


def test_consent_text_migration_converges_when_0675_ran_first(cur):
    """On a database that applied 0675 before 0651 existed, gst.v1 is live
    when 0651 runs; it must end retired with gst.v2 current, as on a fresh one."""
    import importlib.util
    from pathlib import Path

    import clefs.modules.guests as pkg

    path = Path(pkg.__file__).parent / "migrations" / "0651_guests_profile_consent_text.py"
    spec = importlib.util.spec_from_file_location("m0651", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cur.execute("UPDATE consent_texts SET retired_at = NULL WHERE purpose = 'profile_memory' AND version = 'gst.v1'")
    cur.execute("DELETE FROM consent_texts WHERE purpose = 'profile_memory' AND version = 'gst.v2'")
    mod.up(cur)
    mod.up(cur)  # idempotent
    cur.execute(
        "SELECT version, retired_at IS NULL AS live FROM consent_texts WHERE purpose = 'profile_memory' "
        "AND version LIKE 'gst.%%' ORDER BY version, language"
    )
    assert [tuple(r.values()) for r in cur.fetchall()] == [("gst.v1", False)] * 2 + [("gst.v2", True)] * 2
