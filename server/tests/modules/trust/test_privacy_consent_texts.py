"""Consent wording: the seeded platform defaults and a hotel's own versions."""

from __future__ import annotations

from clefs.core import consent
from clefs.modules.trust import consent_texts


def test_migration_seeded_every_purpose_in_english_and_arabic(cur):
    cur.execute("SELECT purpose, language, body FROM consent_texts WHERE hotel_slug IS NULL AND version = %s",
                (consent_texts.VERSION,))
    got = {(r["purpose"], r["language"]): r["body"] for r in cur.fetchall()}
    for purpose in consent.CONSENT_PURPOSES:
        assert (purpose, "en") in got and (purpose, "ar") in got, purpose
    assert all("\u2014" not in body for body in got.values())
    # current_consent_text serves the seeded wording to the concierge and forms.
    assert consent.current_consent_text(cur, "transfer_abroad", language="ar")["version"] == consent_texts.VERSION


def test_seeding_is_idempotent(cur):
    first = consent_texts.seed_consent_texts(cur)
    again = consent_texts.seed_consent_texts(cur)
    assert [r["id"] for r in first] == [r["id"] for r in again]


def test_hotel_adds_its_own_version_and_it_wins(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    owner = client_as("owner")
    body = {"purpose": "marketing", "version": "hotel-v1", "language": "en",
            "body": "We would like to send you our offers. Reply STOP at any time."}
    r = owner.post(f"/hotels/{slug}/trust/consent-texts", json=body)
    assert r.status_code == 201 and r.json()["hotel_slug"] == slug
    assert owner.post(f"/hotels/{slug}/trust/consent-texts", json=body).status_code == 201  # same wording: idempotent
    clash = owner.post(f"/hotels/{slug}/trust/consent-texts", json={**body, "body": "Different wording under the same version"})
    assert clash.status_code == 409 and clash.json()["code"] == "consent_text_immutable"
    rows = client_as("gm").get(f"/hotels/{slug}/trust/consent-texts").json()
    current = [x for x in rows if x["purpose"] == "marketing" and x["language"] == "en" and x["current"]]
    assert len(current) == 1 and current[0]["scope"] == "hotel" and current[0]["version"] == "hotel-v1"
    assert client_as("gm").post(f"/hotels/{slug}/trust/consent-texts", json=body).status_code == 403
    bad = owner.post(f"/hotels/{slug}/trust/consent-texts", json={**body, "purpose": "not_a_purpose", "version": "x2"})
    assert bad.status_code == 400


def test_pii_matrix_route(hotel_uae, client_as):
    r = client_as("gm").get(f"/hotels/{hotel_uae.slug}/trust/pii-matrix")
    assert r.status_code == 200
    body = r.json()
    assert body["classes"]["guest_contact"]["finance"] == "masked" and "audit_ip" in body["classes"]
    assert client_as("front_desk").get(f"/hotels/{hotel_uae.slug}/trust/pii-matrix").status_code == 403
