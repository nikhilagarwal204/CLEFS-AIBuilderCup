"""Public "Audit one OTA statement" (02 spec 3.11, Canon 23): the anonymous
upload through the core/ingest sandbox, the teaser, the lead unlock with
its separate consents, masked guest names, the founder notification, the
stub-safe AI snapshot and the 30-day purge.

The manifest mounts public_audit.router (FOUNDATION-REQUEST in the lane
result until module.py lists it); this module's app includes it when the
manifest does not yet, so the routes are tested either way."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clefs.modules.profit_guard import public_audit as pa

NOW = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)   # 12:00 Dubai, 3 Oct 2026

# Booking.com reservation statement (the alias map's headers). Row 2 is a
# no-show still charged; row 3 carries 18% against the 15% the check
# assumes (L2, CHECK_CONTRACT); rows 1 and 3 have L1 (TO_CONFIRM).
STATEMENT = (
    "Book number,Guest name(s),Check-in,Check-out,Status,Price,Commission %,Commission amount,Currency\n"
    "4000012345,AL MANSOURI/AHMED,01/10/2026,03/10/2026,ok,1000.00,15%,150.00,AED\n"
    "4000012346,Omar Ali,02/10/2026,04/10/2026,no_show,500.00,15%,75.00,AED\n"
    "4000012347,Sara Khan,02/10/2026,04/10/2026,ok,1000.00,18%,180.00,AED\n"
)


@pytest.fixture(scope="module")
def app():
    from clefs.app import create_app

    from tests.foundation.route_walk import route_paths

    a = create_app()
    if "/public/audits" not in route_paths(a.routes):
        a.include_router(pa.router)
    return a


@pytest.fixture(autouse=True)
def frozen(clock, monkeypatch):
    clock.freeze(NOW)
    # The founder's addresses (read through env()); each test sees both.
    monkeypatch.setenv("FOUNDER_NOTIFY_EMAIL", "founder@clefs.test")
    monkeypatch.setenv("FOUNDER_NOTIFY_WHATSAPP", "+971500000001")


def upload(client, data: bytes | str = STATEMENT, *, name="statement.csv", ctype="text/csv", ota="BOOKING",
           consent="true", transfer="true"):
    body = data.encode() if isinstance(data, str) else data
    form = {"consent_processing": consent}
    if transfer is not None:
        form["consent_transfer_abroad"] = transfer
    if ota:
        form["ota_code"] = ota
    return client.post("/public/audits", files={"file": (name, body, ctype)}, data=form)


LEAD = {"name": "Mariam", "hotel_name": "Creek View Hotel", "email": "mariam@creekview.test",
        "whatsapp": "+971501234567", "consent_contact": True, "consent_transfer_abroad": True,
        "consent_index": False}


# ─── Upload, teaser, unlock ───


def test_upload_gives_a_teaser_then_the_lead_unlocks_the_breakdown(client, cur, outbox):
    r = upload(client)
    assert r.status_code == 201, r.text
    body = r.json()
    token = body["token"]
    t = body["teaser"]
    assert body["unlocked"] is False and body["full"] is None and body["ai_snapshot"] is None
    assert t["ota_code"] == "BOOKING" and t["lines"] == 3 and t["currency"] == "AED"
    assert t["commission"] == {"minor": 40500, "amount": "405.00"}
    # Only the no-show's 75.00 counts, at both ends. L2 (30.00) needs the
    # contract and L1 on MF + TD (12.16 + 14.59) is to confirm: rows 1 and 3
    # are 2 lines to confirm, with no amount.
    assert t["found"] == {"low": {"minor": 7500, "amount": "75.00"}, "high": {"minor": 7500, "amount": "75.00"}}
    assert t["by_detector"] == {"L3": 1} and t["findings"] == 1 and t["to_confirm"] == 2
    assert t["open_deadlines"] == 1          # the no-show's 48 h window runs to 6 Oct 00:00 Dubai
    assert "MANSOURI" not in r.text and "Omar" not in r.text

    locked = client.get(f"/public/audits/{token}").json()
    assert locked["teaser"] == t and locked["full"] is None and "token" not in locked

    no = client.post(f"/public/audits/{token}/lead", json={**LEAD, "consent_contact": False})
    assert no.status_code == 422 and no.json()["code"] == "consent_required"
    assert not outbox.captured()

    r2 = client.post(f"/public/audits/{token}/lead", json=LEAD)
    assert r2.status_code == 200, r2.text
    full = r2.json()
    assert full["unlocked"] is True and full["lead"] == {"name": "Mariam", "hotel_name": "Creek View Hotel"}
    assert "mariam@creekview.test" not in r2.text and "+971501234567" not in r2.text
    assert full["ai_snapshot"] == {"status": "unavailable"}
    fs = full["full"]["findings"]
    # The full report keeps every line, each with its public class and calc.
    assert [(f["detector"], f["class"], f["amount_minor"]) for f in fs] == [
        ("L3", "RECOVERABLE", 7500), ("L2", "CHECK_CONTRACT", 3000), ("L1", "TO_CONFIRM", 1459),
        ("L1b", "TO_CONFIRM", 1459), ("L1", "TO_CONFIRM", 1216), ("L1b", "TO_CONFIRM", 1216)]
    assert all(f["calc"] for f in fs)
    assert {f["guest"] for f in fs} == {"O. A.", "S. K.", "A. M. A."}
    assert "MANSOURI" not in r2.text and "Sara" not in r2.text
    # No per-scenario totals: each was an AED sum resting on L1.
    assert "scenarios" not in full["full"] and full["teaser"] == t
    assert full["full"]["assumptions"]["commission_pct"]["BOOKING"] == "15"
    assert {n["detector"] for n in full["full"]["not_checked"]} >= {"L4", "L5", "L10"}

    email = outbox.captured("email")
    wa = outbox.captured("whatsapp")
    assert len(email) == 1 and len(wa) == 1
    assert "AED 75.00, 2 lines to confirm" in str(wa[0]["payload"])
    cur.execute("SELECT * FROM pfg_lead_audits WHERE token_hash = %s", (pa.token_hash(token),))
    row = cur.fetchone()
    assert row["consent_contact"] is True and row["consent_index"] is False and row["notified_founder_at"]
    # QA PUB-8: sealed like the other lead magnets, never plaintext.
    assert row["email"] is None and row["whatsapp_e164"] is None
    assert pa.lead_contacts(dict(row)) == {"email": "mariam@creekview.test", "whatsapp": "+971501234567"}
    assert b"mariam" not in bytes(row["email_enc"])
    assert row["report"]["consents"]["contact"]["version"] == pa.CONSENT_VERSION
    tick = row["report"]["consents"]["lead_transfer_abroad"]
    assert tick["granted"] is True and tick["text"] == pa.CONSENT_TEXTS["lead_transfer_abroad"]
    assert row["consent_lead_transfer_abroad"] is True

    # A second lead post updates the lead but never notifies twice.
    again = client.post(f"/public/audits/{token}/lead", json={**LEAD, "name": "Mariam S."})
    assert again.status_code == 200 and again.json()["lead"]["name"] == "Mariam S."
    assert len(outbox.captured("email")) == 1 and len(outbox.captured("whatsapp")) == 1


def test_lines_to_confirm_never_reach_the_teaser_figures(client, outbox):
    """GTM guard through the real upload route: L1 (fees) and L2 (above the
    assumed percent) stay out of found, the findings count and by_detector.
    With nothing else in the statement the teaser has no AED to show, only
    the lines to confirm; the full report still lists them with their calc."""
    head, ok_row, _no_show, high_pct = STATEMENT.strip().splitlines()
    r = upload(client, "\n".join([head, ok_row, high_pct]) + "\n")
    assert r.status_code == 201, r.text
    t = r.json()["teaser"]
    zero = {"minor": 0, "amount": "0.00"}
    assert t["found"] == {"low": zero, "high": zero}
    assert (t["findings"], t["by_detector"], t["to_confirm"]) == (0, {}, 2)
    full = client.post(f"/public/audits/{r.json()['token']}/lead", json=LEAD).json()["full"]
    assert {(f["detector"], f["class"]) for f in full["findings"]} == {
        ("L1", "TO_CONFIRM"), ("L1b", "TO_CONFIRM"), ("L2", "CHECK_CONTRACT")}
    l2 = next(f for f in full["findings"] if f["detector"] == "L2")
    assert l2["title"] == "Above the percentage this check assumed: check your contract and programme terms"
    assert [s["label"] for s in l2["calc"]] == ["Effective percent", "Assumed percent", "Above the assumed percent"]
    (mail,) = outbox.captured("email")
    assert "Found: AED 0.00;" in str(mail) and "2 lines to confirm" in str(mail)
    # WhatsApp too (the founder's fastest channel): the template keeps its 3
    # params and the count rides in the second, so the lead never looks empty.
    (wa,) = outbox.captured("whatsapp")
    params = [p["text"] for c in wa["payload"]["template"]["components"] for p in c["parameters"]]
    assert params == ["Creek View Hotel", "AED 0.00, 2 lines to confirm", "0"]


# ─── MIME sniff and caps ───


def _xlsx_bytes() -> bytes:
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for line in STATEMENT.strip().splitlines():
        ws.append(line.split(","))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip_bytes() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("notes.txt", "hello")
    return buf.getvalue()


def _pdf_bytes(pages: int) -> bytes:
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_sniff_type_trusts_bytes_not_names():
    assert pa.sniff_type(STATEMENT.encode()) == "csv"
    assert pa.sniff_type(STATEMENT.encode("utf-16")) == "csv"
    assert pa.sniff_type(_xlsx_bytes()) == "xlsx"
    assert pa.sniff_type(_pdf_bytes(1)) == "pdf"
    for bad in (b"MZ\x90\x00" + b"\x00" * 200, b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, _zip_bytes(),
                b"<html><body>a,b</body></html>", b'{"a": 1, "b": 2}', b"\x01\x02\x03,\x04\x05\x06" * 50):
        with pytest.raises(pa.ClefsError) as e:
            pa.sniff_type(bad)
        assert e.value.status == 415 and e.value.code == "unsupported_file_type"
    with pytest.raises(pa.ClefsError) as e:
        pa.sniff_type(b"")
    assert e.value.code == "empty_file"


def test_upload_refuses_a_renamed_binary_and_a_missing_consent(client, cur):
    r = upload(client, b"MZ\x90\x00" + b"\x00" * 500, name="statement.csv")
    assert r.status_code == 415 and r.json()["code"] == "unsupported_file_type"
    r = upload(client, consent="false")
    assert r.status_code == 422 and r.json()["code"] == "consent_required"
    r = upload(client, ota="HOTELBEDS")
    assert r.status_code == 422 and r.json()["code"] == "invalid_ota"
    r = client.post("/public/audits", json={"file": "x"})
    assert r.status_code == 400 and r.json()["code"] == "bad_request"
    cur.execute("SELECT COUNT(*) AS n FROM files WHERE purpose = %s", (pa.FILE_PURPOSE,))
    assert cur.fetchone()["n"] == 0


def test_every_format_needs_the_transfer_abroad_consent_and_both_ticks_are_stored(client, cur, monkeypatch):
    # Review compliance-ota-audit-no-transfer-consent: a CSV is stored on
    # the database in Singapore with its guest names, a PDF is also read by
    # the AI provider in the US. No tick, no upload, nothing stored.
    monkeypatch.setattr(pa, "IP_DAILY_LIMIT", 50)
    for kw in ({"name": "s.pdf", "ctype": "application/pdf"}, {}):
        data = _pdf_bytes(1) if kw else STATEMENT
        r = upload(client, data, transfer=None, **kw)
        assert r.status_code == 422 and r.json()["code"] == "transfer_consent_required"
    cur.execute("SELECT COUNT(*) AS n FROM files WHERE purpose = %s", (pa.FILE_PURPOSE,))
    assert cur.fetchone()["n"] == 0
    # With the tick the PDF goes on to extraction (whatever it then reads).
    r = upload(client, _pdf_bytes(1), name="s.pdf", ctype="application/pdf")
    assert r.json().get("code") != "transfer_consent_required"
    ok = upload(client)
    assert ok.status_code == 201
    cur.execute("SELECT consent_processing, consent_transfer_abroad, report FROM pfg_lead_audits "
                "ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    assert row["consent_processing"] is True and row["consent_transfer_abroad"] is True
    tick = row["report"]["consents"]["transfer_abroad"]
    assert tick["granted"] is True and tick["version"] == pa.UPLOAD_CONSENT_VERSION
    assert "stays in Clefs" not in tick["text"] and "Singapore" in tick["text"]


def test_size_row_and_page_caps(client, monkeypatch):
    big = upload(client, b"a,b\n" + b"1,2\n" * (pa.MAX_BYTES // 4 + 10))
    assert big.status_code == 413 and big.json()["code"] == "file_too_large"
    monkeypatch.setattr(pa, "IP_DAILY_LIMIT", 50)
    monkeypatch.setattr(pa, "MAX_ROWS", 2)
    rows = upload(client)
    assert rows.status_code == 413 and rows.json()["code"] == "too_many_rows"
    pages = upload(client, _pdf_bytes(pa.MAX_PDF_PAGES + 1), name="s.pdf", ctype="application/pdf")
    assert pages.status_code == 413 and pages.json()["code"] == "too_many_pages"


def test_xlsx_upload_reads_like_the_csv(client):
    r = upload(client, _xlsx_bytes(), name="statement.xlsx",
               ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert r.status_code == 201, r.text
    t = r.json()["teaser"]
    assert (t["found"]["high"]["minor"], t["findings"], t["to_confirm"]) == (7500, 1, 2)


def test_rate_limits_per_ip_hash_and_global(client, monkeypatch):
    for _ in range(pa.IP_DAILY_LIMIT):
        assert upload(client).status_code == 201
    r = upload(client)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    monkeypatch.setattr(pa, "IP_DAILY_LIMIT", 100)
    monkeypatch.setattr(pa, "GLOBAL_DAILY_LIMIT", 4)
    assert upload(client).status_code == 201
    r = upload(client)
    assert r.status_code == 429 and "used up" in r.json()["detail"]


def test_an_unreadable_statement_leaves_nothing_behind(client, cur):
    r = upload(client, "Colour,Shape,Size\nred,round,3\nblue,square,4\n", ota=None)
    assert r.status_code == 422 and r.json()["code"] == "unrecognised_columns"
    cur.execute("SELECT COUNT(*) AS n FROM pfg_lead_audits")
    assert cur.fetchone()["n"] == 0
    cur.execute("SELECT COUNT(*) AS n FROM files WHERE purpose = %s", (pa.FILE_PURPOSE,))
    assert cur.fetchone()["n"] == 0
    cur.execute("SELECT COUNT(*) AS n FROM import_batches WHERE hotel_slug IS NULL")
    assert cur.fetchone()["n"] == 0


# ─── AI snapshot (stub-safe), index consent, purge ───


def test_unlock_starts_the_ai_snapshot_and_shows_it_when_visibility_answers(client, monkeypatch):
    from clefs.modules.visibility import api as visibility_api

    seen = {}

    def start(cur, *, subject, requester_ref):
        seen["subject"], seen["ref"] = subject, requester_ref
        return 77

    snap = {"status": "ready", "per_engine": [{"engine": "gemini_web_search", "mentioned": 2, "runs": 9, "ci": None}],
            "top_cited_domains": ["booking.com"], "answers_about_you": [], "label": "API simulation"}
    monkeypatch.setattr(visibility_api, "start_ai_snapshot", start)
    monkeypatch.setattr(visibility_api, "get_ai_snapshot", lambda cur, batch_id: snap if batch_id == 77 else None)
    token = upload(client).json()["token"]
    body = client.post(f"/public/audits/{token}/lead",
                       json={**LEAD, "city": "Dubai", "area": "Deira", "star": 4}).json()
    assert body["ai_snapshot"] == snap
    assert seen["subject"].hotel_name == "Creek View Hotel" and seen["subject"].area == "Deira"
    assert seen["subject"].star == 4 and seen["ref"].startswith("pg.audit:")
    assert client.get(f"/public/audits/{token}").json()["ai_snapshot"] == snap


def test_a_failing_snapshot_never_breaks_the_unlock(client, monkeypatch):
    from clefs.modules.visibility import api as visibility_api

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(visibility_api, "start_ai_snapshot", boom)
    token = upload(client).json()["token"]
    r = client.post(f"/public/audits/{token}/lead", json=LEAD)
    assert r.status_code == 200 and r.json()["ai_snapshot"] == {"status": "unavailable"}


def test_lead_validation(client):
    token = upload(client).json()["token"]
    for patch, code in (({"email": None, "whatsapp": None}, "invalid_input"),
                        ({"email": "not-an-email", "whatsapp": None}, "invalid_input"),
                        ({"whatsapp": "0501234567", "email": None}, "invalid_input"),
                        ({"hotel_name": "x"}, "invalid_input"), ({"name": ""}, "invalid_input"),
                        ({"consent_transfer_abroad": False}, "transfer_consent_required")):
        r = client.post(f"/public/audits/{token}/lead", json={**LEAD, **patch})
        assert r.status_code == 422 and r.json()["code"] == code, (patch, r.text)
    assert client.get("/public/audits/nope").status_code == 404
    assert client.post("/public/audits/nope/lead", json=LEAD).json()["code"] == "not_found"


def test_index_consent_adds_one_anonymised_sample_and_withdrawal_removes_it(client, cur):
    token = upload(client).json()["token"]
    client.post(f"/public/audits/{token}/lead", json={**LEAD, "consent_index": True, "city": "Abu Dhabi"})
    cur.execute("SELECT * FROM pfg_index_samples")
    rows = cur.fetchall()
    assert len(rows) == 1
    s = rows[0]
    assert (s["ota_code"], s["emirate"], s["property_band"], s["lines"]) == ("BOOKING", "AUH", "3-star", 3)
    # Only what the headline counts: the no-show (75.00 of 405.00). L2 is
    # CHECK_CONTRACT: a line to confirm, not counted in the index.
    assert (s["commission_minor"], s["leak_minor"], str(s["leak_rate"])) == (40500, 7500, "0.185185")
    assert s["detector_counts"] == {"L3": {"n": 1, "minor": 7500}}
    # The same bytes audited again (same staged batch) never count twice.
    token2 = upload(client).json()["token"]
    client.post(f"/public/audits/{token2}/lead", json={**LEAD, "consent_index": True})
    cur.execute("SELECT COUNT(*) AS n FROM pfg_index_samples")
    assert cur.fetchone()["n"] == 1
    client.post(f"/public/audits/{token}/lead", json={**LEAD, "consent_index": False})
    cur.execute("SELECT COUNT(*) AS n FROM pfg_index_samples")
    assert cur.fetchone()["n"] == 0


def test_purge_at_30_days_keeps_only_the_anonymised_sample(client, cur, clock, run_jobs):
    token = upload(client).json()["token"]
    client.post(f"/public/audits/{token}/lead", json={**LEAD, "consent_index": True})
    cur.execute("SELECT id, file_id, batch_id FROM pfg_lead_audits")
    a = cur.fetchone()
    clock.advance(days=29)
    assert pa.purge_expired(cur)["audits"] == 0
    assert client.get(f"/public/audits/{token}").status_code == 200
    clock.advance(days=1, minutes=1)
    assert client.get(f"/public/audits/{token}").status_code == 404
    rep = run_jobs([pa.PURGE_JOB], scheduled=True)
    cur.execute("SELECT COUNT(*) AS n FROM pfg_lead_audits")
    assert cur.fetchone()["n"] == 0, rep
    cur.execute("SELECT deleted_at FROM files WHERE id = %s", (a["file_id"],))
    assert cur.fetchone()["deleted_at"] is not None
    cur.execute("SELECT COUNT(*) AS n FROM import_batches WHERE id = %s", (a["batch_id"],))
    assert cur.fetchone()["n"] == 0
    cur.execute("SELECT COUNT(*) AS n FROM import_rows WHERE batch_id = %s", (a["batch_id"],))
    assert cur.fetchone()["n"] == 0
    cur.execute("SELECT audit_id, leak_minor FROM pfg_index_samples")
    assert [dict(r) for r in cur.fetchall()] == [{"audit_id": None, "leak_minor": 7500}]


def test_leakage_index_route_hides_figures_below_thirty(client, cur):
    token = upload(client).json()["token"]
    client.post(f"/public/audits/{token}/lead", json={**LEAD, "consent_index": True})
    r = client.get("/public/leakage-index")
    assert r.status_code == 200
    body = r.json()
    assert body["published"] is False and body["n"] == 1 and body["needed"] == 29
    assert "median_leak_rate" not in body and body["methodology"]["measure"].startswith("Leak rate")


def test_founder_email_carries_the_contacts_and_legacy_rows_get_sealed(client, cur, outbox):
    """QA PUB-8: sealed at rest, still in the founder's alert; a row
    unlocked before 0303 (plaintext) is sealed by the daily purge job."""
    token = upload(client).json()["token"]
    assert client.post(f"/public/audits/{token}/lead", json=LEAD).status_code == 200
    (mail,) = outbox.captured("email")
    assert "mariam@creekview.test" in str(mail) and "+971501234567" in str(mail)
    cur.execute("UPDATE pfg_lead_audits SET email = 'old@lead.test', whatsapp_e164 = '+971509999999', "
                "email_enc = NULL, whatsapp_enc = NULL WHERE token_hash = %s", (pa.token_hash(token),))
    assert pa.seal_legacy_contacts(cur) == 1
    cur.execute("SELECT * FROM pfg_lead_audits WHERE token_hash = %s", (pa.token_hash(token),))
    row = dict(cur.fetchone())
    assert row["email"] is None and row["whatsapp_e164"] is None
    assert pa.lead_contacts(row) == {"email": "old@lead.test", "whatsapp": "+971509999999"}


def test_detect_from_the_file_names_booking_for_the_seed_statement(client, cur, monkeypatch):
    """QA PUB-1: 'Detect it from the file' on the demo's own Booking.com
    export ("Booking number" header) fell to the generic source: OTA not
    recognised, 1 finding instead of 86."""
    from pathlib import Path

    seed = Path(pa.__file__).resolve().parents[2] / "seed" / "demo_uae" / "files" / \
        "booking-statement-BDC-202608-40136.csv"
    if not seed.exists():
        pytest.skip("demo seed files not present")
    monkeypatch.setattr(pa, "IP_DAILY_LIMIT", 50)
    auto = upload(client, seed.read_bytes(), name=seed.name, ota=None)
    named = upload(client, seed.read_bytes(), name=seed.name, ota="BOOKING")
    assert auto.status_code == 201, auto.text
    assert auto.json()["teaser"]["ota_code"] == "BOOKING"
    # The rate and fee checks need the OTA. Since the GTM guard their lines
    # are to confirm, not findings, so that is where the proof they ran is.
    a, n = auto.json()["teaser"], named.json()["teaser"]
    assert (a["findings"], a["to_confirm"]) == (n["findings"], n["to_confirm"]) and a["to_confirm"] > 10


def test_guess_source_needs_the_booking_number_and_the_commission():
    assert pa.guess_source(["Booking number", "Check in", "Check out", "Total price", "Commission amount payable",
                            "Commissionable price"]) == ("booking_res_statement", "BOOKING")
    assert pa.guess_source(["Check in", "Check out", "Total price", "Status", "Rooms"]) is None
