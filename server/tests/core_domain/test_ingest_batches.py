"""core/ingest batches against the real schema: upload, alias and template
mapping, the AI proposal and its confirmation, the CSV totals gate, review
edits, commit through the handler registry, supersede and discard."""

from __future__ import annotations

import pytest

from tests.core_domain.conftest import swap_commit_handler

from clefs import files
from clefs.core.errors import ClefsError
from clefs.core.ingest import batches, commit, kinds, mapping

STATEMENT = (
    "Reservation statement 2025-10\n"
    "Book number,Guest name(s),Check-in,Check-out,Status,Price,Commission %,Commission amount,Currency\n"
    "5001,Jane Doe,01/10/2025,03/10/2025,ok,1000.00,15%,150.00,AED\n"
    "5002,Omar Ali,02/10/2025,04/10/2025,no_show,500.00,15%,75.00,AED\n"
    "Total,,,,,1500.00,,225.00,\n"
)


def put(cur, hotel, text: str | bytes, name: str = "stmt.csv", ctype: str = "text/csv") -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return files.put_file(cur, hotel, data, filename=name, content_type=ctype, purpose="ingest.upload").id


@pytest.fixture
def ctx_owner(cur, make_user, hotel_uae):
    from clefs.auth.deps import Principal, resolve_ctx

    user = make_user("owner", hotel_uae.slug)
    principal = Principal(kind="user", user_id=user["id"], label="Test owner", is_clefs_admin=False,
                          session_kind="web")
    return resolve_ctx(principal, hotel_uae.slug, cur=cur)


@pytest.fixture
def received():
    """A test commit handler for OTA_STATEMENT that records its calls."""
    calls: list[tuple[dict, list[dict]]] = []

    def handler(cur, batch, rows):
        calls.append((batch, rows))
        return {"lines": len(rows)}

    with swap_commit_handler("OTA_STATEMENT", handler):
        yield calls


def test_booking_statement_maps_by_alias_and_passes_the_totals_gate(cur, hotel_uae):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT),
                             source_code="booking_res_statement")
    assert b["created"] and b["status"] == "REVIEW", b["errors"]
    assert (b["kind"], b["ota_code"], b["mapping_source"], b["header_row"]) == ("OTA_STATEMENT", "BOOKING", "alias", 1)
    assert b["printed_total_minor"] == 22500 and b["parsed_total_minor"] == 22500 and b["gate_status"] == "ok"
    assert str(b["period_start"]) == "2025-10-01" and str(b["period_end"]) == "2025-10-04"
    assert b["stats"]["unverified_aliases"] is True
    rows = batches.list_rows(cur, b["id"])
    assert [r["canonical"]["line_status"] for r in rows] == ["OK", "NO_SHOW"]
    again = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT))
    assert again["id"] == b["id"] and again["created"] is False  # same bytes, one batch


def test_totals_mismatch_blocks_commit_until_overridden(cur, hotel_uae, ctx_owner, received):
    text = STATEMENT.replace("Total,,,,,1500.00,,225.00,", "Total,,,,,1500.00,,240.00,")
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, text),
                             source_code="booking_res_statement")
    assert b["gate_status"] == "mismatch"
    with pytest.raises(ClefsError) as e:
        commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
    assert e.value.code == "gate_blocked"
    with pytest.raises(ClefsError):
        commit.override_gate(cur, hotel_uae.slug, b["id"], actor=ctx_owner, reason="")
    commit.override_gate(cur, hotel_uae.slug, b["id"], actor=ctx_owner, reason="OTA rounding per line")
    done = commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
    assert done["status"] == "COMMITTED" and done["commit_result"] == {"lines": 2}
    cur.execute("SELECT action, meta FROM audit_log WHERE entity_type = 'import_batch' AND entity_id = %s ORDER BY id",
                (str(b["id"]),))
    audits = [(r["action"], r["meta"]) for r in cur.fetchall()]
    assert [a for a, _ in audits] == ["ingest.gate_override", "ingest.commit"]
    assert audits[1][1]["gate_overridden"] is True


def test_commit_hands_canonical_rows_to_the_registered_handler(cur, hotel_uae, ctx_owner, received):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT),
                             source_code="booking_res_statement")
    commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
    [(batch, rows)] = received
    assert batch["id"] == b["id"] and batch["ota_code"] == "BOOKING"
    assert rows[0]["ota_res_id"] == "5001" and rows[0]["commission_minor"] == 15000
    assert rows[0]["_row_no"] == 1 and isinstance(rows[0]["_row_id"], int)
    with pytest.raises(ClefsError) as e:
        commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
    assert e.value.code == "already_committed"


def test_no_handler_and_error_rows_refuse_the_commit(cur, hotel_uae, ctx_owner, received):
    kinds.unregister_commit_handler("OTA_STATEMENT")
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT),
                             source_code="booking_res_statement")
    with pytest.raises(ClefsError) as e:
        commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
    assert e.value.code == "no_commit_handler"
    kinds.register_commit_handler("OTA_STATEMENT", lambda cur, batch, rows: None)
    bad = STATEMENT.replace("02/10/2025,04/10/2025", "soon,04/10/2025").replace("225.00", "150.00")
    b2 = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, bad),
                              source_code="booking_res_statement")
    assert b2["gate_status"] == "ok"  # the ERROR row is not in the parsed total
    with pytest.raises(ClefsError) as e:
        commit.commit_batch(cur, hotel_uae.slug, b2["id"], actor=ctx_owner)
    assert e.value.code == "rows_have_errors"
    done = commit.commit_batch(cur, hotel_uae.slug, b2["id"], actor=ctx_owner, skip_errors=True)
    assert done["status"] == "COMMITTED" and done["commit_result"] == {"rows": 1}


ODD = (
    "Ref,Pax,Arr,Dep,Gross amt,Comm amt\n"
    "A1,Jane Doe,01/10/2025,03/10/2025,1000,150\n"
    "A2,Omar Ali,02/10/2025,04/10/2025,500,75\n"
)
ODD_MONTH2 = ODD.replace("A1,", "B1,").replace("A2,", "B2,")


def _proposal():
    return {
        "mappings": [
            {"canonical": "ota_res_id", "header": "Ref", "transform": "none", "transform_value": "", "confidence": 0.9},
            {"canonical": "guest_name", "header": "Pax", "transform": "none", "transform_value": "", "confidence": 0.8},
            {"canonical": "check_in", "header": "Arr", "transform": "date_fmt", "transform_value": "DD/MM/YYYY",
             "confidence": 0.9},
            {"canonical": "check_out", "header": "Dep", "transform": "none", "transform_value": "", "confidence": 0.9},
            {"canonical": "gross_minor", "header": "Gross amt", "transform": "none", "transform_value": "",
             "confidence": 0.95},
            {"canonical": "commission_minor", "header": "Comm amt", "transform": "none", "transform_value": "",
             "confidence": 0.95},
            {"canonical": "booker_country", "header": "Nowhere", "transform": "none", "transform_value": "",
             "confidence": 0.9},
        ],
        "unmapped": ["line_status"],
    }


def test_unknown_layout_asks_the_ai_with_masked_samples_then_saves_a_template(cur, hotel_uae, fake_ai):
    fake_ai.push(_proposal())
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, ODD), kind="OTA_STATEMENT")
    assert b["status"] == "MAPPING_REQUIRED" and b["stats"]["missing"]
    assert batches.list_rows(cur, b["id"]) == []  # nothing staged before a person confirms
    [call] = fake_ai.calls
    assert call.task == mapping.AI_TASK and "Jane" not in call.input and "Omar" not in call.input
    s = b["mapping_suggestion"]
    assert s["mapping"]["check_in"] == "Arr" and s["transforms"] == {"check_in": {"date_fmt": "DD/MM/YYYY"}}
    assert "booker_country" not in s["mapping"]  # a header that does not exist is dropped
    done = batches.confirm_mapping(cur, hotel_uae.slug, b["id"], mapping=s["mapping"], transforms=s["transforms"],
                                   confirmed_by=None)
    assert done["status"] == "REVIEW" and done["mapping_source"] == "ai" and done["row_count"] == 2
    tpl = mapping.find_template(cur, hotel_uae.slug, done["header_fingerprint"], kind="OTA_STATEMENT")
    assert tpl["origin"] == "ai" and tpl["version"] == 1 and tpl["mapping"] == s["mapping"]
    # Next month, same layout: the template maps it and no AI call is made.
    nxt = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, ODD_MONTH2),
                               kind="OTA_STATEMENT")
    assert nxt["status"] == "REVIEW" and nxt["mapping_source"] == "template"
    assert nxt["mapping_template_id"] == tpl["id"] and len(fake_ai.calls) == 1


def test_ai_off_leaves_the_mapping_to_a_person(cur, hotel_uae, fake_ai):
    from clefs import ai

    fake_ai.push(ai.AIDisabled())
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, ODD), kind="OTA_STATEMENT")
    assert b["status"] == "MAPPING_REQUIRED" and b["mapping_suggestion"] is None
    with pytest.raises(ClefsError) as e:
        batches.confirm_mapping(cur, hotel_uae.slug, b["id"], mapping={"ota_res_id": "Ref"})
    assert e.value.code == "mapping_incomplete"
    with pytest.raises(ClefsError) as e:
        batches.confirm_mapping(cur, hotel_uae.slug, b["id"], mapping={"ota_res_id": "Ref", "check_in": "Ref"})
    assert e.value.code == "invalid_mapping"
    manual = {"ota_res_id": "Ref", "check_in": "Arr", "gross_minor": "Gross amt", "commission_minor": "Comm amt"}
    done = batches.confirm_mapping(cur, hotel_uae.slug, b["id"], mapping=manual)
    assert done["status"] == "REVIEW" and done["mapping_source"] == "manual"
    changed = batches.confirm_mapping(cur, hotel_uae.slug, b["id"], mapping={**manual, "guest_name": "Pax"})
    tpl = mapping.find_template(cur, hotel_uae.slug, changed["header_fingerprint"])
    assert tpl["version"] == 2 and tpl["mapping"]["guest_name"] == "Pax"


def test_row_edits_reread_only_the_edited_field(cur, hotel_uae):
    bad = STATEMENT.replace("02/10/2025,04/10/2025", "soon,04/10/2025")
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, bad),
                             source_code="booking_res_statement")
    rows = batches.list_rows(cur, b["id"])
    assert rows[1]["status"] == "ERROR"
    fixed = batches.edit_row(cur, hotel_uae.slug, b["id"], rows[1]["id"], values={"check_in": "02/10/2025"})
    assert fixed["status"] == "OK" and fixed["canonical"]["check_in"] == "2025-10-02"
    assert fixed["canonical"]["commission_minor"] == 7500  # minor units were not read again as major units
    assert batches.get_batch(cur, hotel_uae.slug, b["id"])["gate_status"] == "ok"
    with pytest.raises(ClefsError) as e:
        batches.edit_row(cur, hotel_uae.slug, b["id"], rows[0]["id"], values={"check_in": "never"})
    assert e.value.code == "invalid_value"
    skipped = batches.edit_row(cur, hotel_uae.slug, b["id"], rows[0]["id"], skip=True)
    assert skipped["status"] == "SKIP"
    after = batches.get_batch(cur, hotel_uae.slug, b["id"])
    assert after["parsed_total_minor"] == 7500 and after["gate_status"] == "mismatch"


def test_a_corrected_reupload_supersedes_the_earlier_batch(cur, hotel_uae, ctx_owner, received):
    first = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT),
                                 source_code="booking_res_statement")
    commit.commit_batch(cur, hotel_uae.slug, first["id"], actor=ctx_owner)
    corrected = STATEMENT.replace("Jane Doe", "Jane Doe-Smith")
    second = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, corrected),
                                  source_code="booking_res_statement")
    assert second["supersedes_batch_id"] == first["id"]
    commit.commit_batch(cur, hotel_uae.slug, second["id"], actor=ctx_owner)
    old = batches.get_batch(cur, hotel_uae.slug, first["id"])
    assert old["status"] == "SUPERSEDED" and old["superseded_by"] == second["id"]
    assert received[1][0]["supersedes_batch_id"] == first["id"]  # the handler knows what it replaces


def test_discard_frees_the_bytes_for_a_new_upload(cur, hotel_uae, ctx_owner):
    fid = put(cur, hotel_uae.slug, STATEMENT)
    b = batches.create_batch(cur, hotel_uae.slug, file_id=fid, source_code="booking_res_statement")
    commit.discard_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
    with pytest.raises(ClefsError):
        batches.get_batch(cur, hotel_uae.slug, b["id"])
    again = batches.create_batch(cur, hotel_uae.slug, file_id=fid, source_code="booking_res_statement")
    assert again["created"] and again["id"] != b["id"]


def test_bad_files_fail_with_a_reason_and_can_be_rerun(cur, hotel_uae):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, "just one line\n"),
                             kind="PMS_FOLIO")
    assert b["status"] == "FAILED" and b["errors"][0]["code"] == "no_header_row"
    x = batches.create_batch(cur, hotel_uae.slug, kind="SUPPLIER_INVOICE",
                             file_id=put(cur, hotel_uae.slug, b"<?xml version='1.0'?><Invoice/>", "e.xml", "text/xml"))
    assert x["status"] == "FAILED" and x["errors"][0]["code"] == "xml_not_supported"
    with pytest.raises(ClefsError) as e:
        batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, "a,b\n1,2\n", "x.csv"))
    assert e.value.code == "unknown_kind"
    with pytest.raises(ClefsError) as e:
        batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, "a,b\n1,3\n"), kind="TRIAL_BALANCE")
    assert e.value.code == "period_month_required"


def test_auto_commit_needs_a_template_a_clean_gate_and_a_handler(cur, hotel_uae, received, fake_ai):
    fake_ai.push(_proposal())
    first = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, ODD), kind="OTA_STATEMENT")
    s = first["mapping_suggestion"]
    batches.confirm_mapping(cur, hotel_uae.slug, first["id"], mapping=s["mapping"], transforms=s["transforms"])
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, ODD_MONTH2),
                             kind="OTA_STATEMENT", auto_commit=True)
    assert commit.maybe_auto_commit(cur, b)["status"] == "COMMITTED"
    alias = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT),
                                 source_code="booking_res_statement", auto_commit=True)
    assert commit.maybe_auto_commit(cur, alias)["status"] == "REVIEW"  # an unconfirmed alias map waits


# ─── Mailed imports and sender trust ───


def _mail(cur, hotel, sender: str, headers: dict[str, str], text: str, name: str):
    from clefs import notify

    ref = files.put_file(cur, hotel, text.encode(), filename=name, content_type="text/csv", purpose="ingest.upload")
    return notify.InboundEmail(cur=cur, inbound_id=None, hotel_slug=hotel, purpose="ingest", purpose_token=None,
                               ref="ota_statement", alias="testdubai", sender=sender, sender_name=None, to="x",
                               subject=None, text=None, html=None, stripped_reply=None, message_id=f"m-{name}",
                               attachments=[ref], headers=headers)


def test_mailed_import_auto_commits_only_from_a_proven_vouched_sender(cur, hotel_uae, make_user, received,
                                                                       fake_ai, run_jobs):
    """The alias is guessable: a CSV that mimics the hotel's saved layout
    once auto-committed (and could supersede real data) from anyone. Now it
    waits in REVIEW unless From is proven (aligned SPF/DKIM/DMARC) and is a
    member or on the owner's sender list."""
    from clefs.core.ingest import inbound

    fake_ai.push(_proposal())
    first = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, ODD), kind="OTA_STATEMENT")
    s = first["mapping_suggestion"]
    batches.confirm_mapping(cur, hotel_uae.slug, first["id"], mapping=s["mapping"], transforms=s["transforms"])
    make_user("finance", hotel_uae.slug, email="finance@hotel.ae")
    spf = {"Received-SPF": 'Pass (sender SPF authorized) identity=mailfrom; envelope-from="bounce@hotel.ae"; '
                           'receiver=inbound.resend.com'}

    def status_of(msg) -> tuple[bool, str]:
        out = inbound.on_ingest_email(msg)
        run_jobs("ingest.process")
        [bid] = out["batches"]
        cur.execute("SELECT auto_commit, status FROM import_batches WHERE id = %s", (bid,))
        row = cur.fetchone()
        return row["auto_commit"], row["status"]

    # A forged From (no authentication result) of a real member waits.
    forged = ODD_MONTH2.replace("B1,", "C1,")
    assert status_of(_mail(cur, hotel_uae.slug, "finance@hotel.ae", {}, forged, "forged.csv")) == (False, "REVIEW")
    # A proven sender the hotel does not know waits too.
    stranger = ODD_MONTH2.replace("B1,", "D1,")
    spf_x = {"Received-SPF": 'Pass identity=mailfrom; envelope-from="x@elsewhere.test"; '
                             'receiver=inbound.resend.com'}
    assert status_of(_mail(cur, hotel_uae.slug, "x@elsewhere.test", spf_x, stranger, "x.csv")) == (False, "REVIEW")
    # A proven active member commits without a person.
    assert status_of(_mail(cur, hotel_uae.slug, "Finance <finance@hotel.ae>", spf, ODD_MONTH2, "ok.csv")) == (
        True, "COMMITTED")
    # The owner's list vouches for a non-member (a PMS report mailer).
    cur.execute("INSERT INTO hotel_module_settings (hotel_slug, module, settings) VALUES (%s, 'ingest', %s)",
                (hotel_uae.slug, '{"email_senders": ["@pms.example"]}'))
    pms = ODD_MONTH2.replace("B1,", "E1,").replace("B2,", "E2,")
    spf_p = {"Received-SPF": 'Pass identity=mailfrom; envelope-from="bounce@pms.example"; '
                             'receiver=inbound.resend.com'}
    assert status_of(_mail(cur, hotel_uae.slug, "reports@pms.example", spf_p, pms, "p.csv")) == (True, "COMMITTED")


def test_post_commit_hooks_survive_handler_replacement(cur, hotel_uae, ctx_owner, received):
    """A module reacting to commits registers a hook, not a wrapper, so
    replacing the handler (a re-import, a test) can never drop it."""
    seen: list[int] = []

    def hook(cur, batch, result):
        seen.append(batch["id"])

    kinds.register_post_commit_hook("OTA_STATEMENT", hook)
    try:
        kinds.register_commit_handler("OTA_STATEMENT", lambda cur, batch, rows: {"lines": len(rows)})
        b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, STATEMENT),
                                 source_code="booking_res_statement")
        commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner, dry_run=True)
        assert seen == []  # dry runs never fire hooks
        commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=ctx_owner)
        assert seen == [b["id"]]
    finally:
        with kinds._lock:
            kinds._post_hooks.get("OTA_STATEMENT", {}).pop(f"{hook.__module__}.{hook.__qualname__}", None)
