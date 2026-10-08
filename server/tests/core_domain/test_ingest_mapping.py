"""core/ingest alias maps, transforms, staging (P&L month split, dedupe)
and the masking of AI samples. Pure: no database."""

from __future__ import annotations

from pathlib import Path

import pytest

from clefs.core.ingest import aliases, mapping, sniff, stage, transforms as tf
from clefs.core.ingest.kinds import KINDS, SCHEMAS

ALIAS_DIR = Path(aliases.__file__).parent / "aliases"


# ─── Alias maps ───


def test_every_alias_map_loads_and_is_marked_unverified():
    maps = aliases.load_maps()
    assert {"booking_res_statement", "booking_payouts", "agoda_finance", "expedia_reservations",
            "pms_reservations", "pms_folio", "pms_occupancy", "issuance", "generic", "pl_statement",
            "trial_balance", "booking_engine_attribution"} <= set(maps)
    for m in maps.values():
        assert m.unverified, m.source_code
        text = (ALIAS_DIR / f"{m.source_code}.yaml").read_text(encoding="utf-8")
        assert "UNVERIFIED" in text, m.source_code
        assert "\u2014" not in text  # house style: no em dash
    assert set(maps["generic"].by_kind) <= set(KINDS)


def test_a_typo_in_an_alias_file_fails_loudly(tmp_path, monkeypatch):
    (tmp_path / "bad_source.yaml").write_text(
        "source_code: bad_source\nkind: OTA_STATEMENT\nfields:\n  ota_res_idd: [x]\n", encoding="utf-8")
    monkeypatch.setattr(aliases, "ALIAS_DIR", tmp_path)
    aliases.load_maps.cache_clear()
    try:
        with pytest.raises(ValueError, match="ota_res_idd"):
            aliases.load_maps()
    finally:
        monkeypatch.undo()
        aliases.load_maps.cache_clear()


BOOKING_HEADERS = ["Book number", "Booked by", "Guest name(s)", "Check-in", "Check-out", "Booked on", "Status",
                   "Rooms", "Persons", "Price", "Commission %", "Commission amount", "Currency", "Remarks"]


def test_booking_statement_maps_every_required_field():
    mp, tr = aliases.resolve(BOOKING_HEADERS, "OTA_STATEMENT", "booking_res_statement")
    assert mp["ota_res_id"] == "Book number" and mp["guest_name"] == "Guest name(s)"
    assert mp["gross_minor"] == "Price" and mp["commission_minor"] == "Commission amount"
    assert mp["commission_pct"] == "Commission %" and tr == {"commission_pct": {"pct_to_ratio": True}}
    assert aliases.missing_required(mp, "OTA_STATEMENT") == []
    assert len(set(mp.values())) == len(mp)  # one header, one field


def test_detect_source_by_headers():
    assert aliases.detect_source(BOOKING_HEADERS) == "booking_res_statement"
    assert aliases.detect_source(["Booking ID", "Customer name", "Check-in", "Total inclusive", "Commission",
                                  "Net inclusive"]) == "agoda_finance"
    assert aliases.detect_source(["Confirmation No", "Arrival", "Departure", "Guest Name"]) == "pms_reservations"
    assert aliases.detect_source(["Foo", "Bar"]) is None
    assert aliases.source_kind("pms_folio") == "PMS_FOLIO"


def test_contains_pass_and_generic_fallback():
    mp, _ = aliases.resolve(["Reservation ID", "Arrival date (local)", "Departure date (local)"], "PMS_RESERVATIONS")
    assert mp == {"pms_res_id": "Reservation ID", "check_in": "Arrival date (local)",
                  "check_out": "Departure date (local)"}
    # The canonical names themselves always map (a Clefs template file).
    mp, _ = aliases.resolve(["value_date", "amount_minor", "reference"], "BANK")
    assert mp == {"value_date": "value_date", "amount_minor": "amount_minor", "reference": "reference"}


# ─── Transforms ───


@pytest.mark.parametrize(
    ("value", "typ", "t", "expected"),
    [
        ("01/10/2025", "date", None, "2025-10-01"),  # UAE exports are day first
        ("10/01/2025", "date", {"date_fmt": "MM/DD/YYYY"}, "2025-10-01"),
        ("2025-10-01", "date", None, "2025-10-01"),
        (45931, "date", None, "2025-10-01"),  # an Excel serial that lost its format
        ("1 Oct 2025", "date", None, "2025-10-01"),
        ("1.234,50", "money", None, 123450),
        ("1,234.50", "money", None, 123450),
        ("1,234", "money", None, 123400),
        ("12,5", "money", None, 1250),
        ("(12.00)", "money", None, -1200),
        ("AED 30", "money", None, 3000),
        ("100", "money", {"sign_flip": True}, -10000),
        ("1.234", "money", {"decimal_sep": ","}, 123400),
        ("15%", "pct", None, "0.15"),
        ("18", "pct", None, "0.18"),
        ("0.5", "pct", {"pct_to_ratio": True}, "0.005"),
        ("0.18", "pct", None, "0.18"),
        ("cancelled_by_guest", "status", None, "CANCELLED"),
        ("No Show", "status", None, "NO_SHOW"),
        (123456.0, "text", None, "123456"),
        ("Oct 2025", "month", None, "2025-10-01"),
        ("د.إ", "currency", None, "AED"),
    ],
)
def test_coerce(value, typ, t, expected):
    got, warn = tf.coerce(value, typ, currency="AED", transforms=t, status_map=SCHEMAS["OTA_STATEMENT"].status_map)
    assert got == expected and warn is None


def test_coerce_errors_and_warnings():
    with pytest.raises(ValueError):
        tf.coerce("soon", "date", currency="AED")
    with pytest.raises(ValueError):
        tf.coerce("abc", "money", currency="AED")
    got, warn = tf.coerce("Pending review", "status", currency="AED", status_map={"ok": "OK"})
    assert got == "PENDING REVIEW" and "unknown status" in warn
    assert tf.coerce("  ", "money", currency="AED") == (None, None)
    assert tf.parse_money("1.5", "KWD") == 1500  # three minor digits


@pytest.mark.parametrize(
    "cell, minor",
    [
        # A dotted currency abbreviation once left its "." in the body, so
        # "Dhs. 1,200" read as European 1.200 (AED 1.20) and "Rs. 1,500.00"
        # failed outright.
        ("Dhs. 1,200", 120000),
        ("Dhs. 1,200.50", 120050),
        ("Rs. 1,500.00", 150000),
        ("Rs.1,500", 150000),
        ("1,200 Dhs.", 120000),
        # A sign inside the currency once vanished with the letters, turning
        # refunds and chargebacks into revenue.
        ("AED -30.00", -3000),
        ("USD -1,234.50", -123450),
        ("$-50", -5000),
        ("-$50", -5000),
        ("-AED 30", -3000),
        ("AED - 30", -3000),
        ("AED (12.00)", -1200),
        ("(AED 12.00)", -1200),
        ("30.00-", -3000),
        # What already worked keeps working.
        ("AED 30", 3000),
        ("30 AED", 3000),
        ("د.إ 30", 3000),
        ("€1.234,56", 123456),
        ("1 234,50", 123450),
        (".50", 50),
    ],
)
def test_money_cells_with_currency_tokens(cell, minor):
    assert tf.parse_money(cell, "AED") == minor
    got, warn = tf.coerce(cell, "money", currency="AED")
    assert (got, warn) == (minor, None)


@pytest.mark.parametrize("cell", ["12abc34", "2025-10-01", "AED", "-", "Dhs."])
def test_junk_inside_a_number_is_an_error_not_dropped(cell):
    with pytest.raises(ValueError):
        tf.parse_decimal(cell)


# ─── Staging ───


def _table(text: str) -> sniff.Table:
    return sniff.sniff(text.encode("utf-8"))


def test_statement_rows_stage_with_statuses_and_duplicates():
    t = _table(
        "Book number,Guest name(s),Check-in,Status,Price,Commission %,Commission amount,Currency\n"
        "123,Jane,01/10/2025,ok,1234.50,15%,185.18,AED\n"
        "123,Jane,01/10/2025,ok,1234.50,15%,185.18,AED\n"
        "124,Bob,soon,no_show,500,15,75,AED\n"
        "125,Ann,03/10/2025,pending review,300,15,45,USD\n"
    )
    mp, tr = aliases.resolve(t.headers, "OTA_STATEMENT", "booking_res_statement")
    rows = stage.stage_table(t, "OTA_STATEMENT", mp, tr, currency="AED")
    assert [r.status for r in rows] == ["OK", "DUP", "ERROR", "WARN"]
    assert rows[0].canonical == {"ota_res_id": "123", "guest_name": "Jane", "check_in": "2025-10-01",
                                 "line_status": "OK", "gross_minor": 123450, "commission_pct": "0.15",
                                 "commission_minor": 18518, "currency": "AED"}
    assert rows[1].messages == ["same record as row 1"]
    assert rows[2].messages == ["check_in: 'soon' is not a date"]
    assert rows[3].canonical["currency"] == "USD" and "unknown status" in rows[3].messages[0]
    assert rows[0].raw["Guest name(s)"] == "Jane"


def test_missing_required_value_is_an_error_row():
    t = _table("Book number,Check-in,Price,Commission amount\n,01/10/2025,100,15\n")
    mp, tr = aliases.resolve(t.headers, "OTA_STATEMENT", "booking_res_statement")
    [row] = stage.stage_table(t, "OTA_STATEMENT", mp, tr, currency="AED")
    assert row.status == "ERROR" and row.messages == ["ota_res_id: missing"]


def test_pl_with_month_columns_splits_rows_and_signs_by_section():
    t = _table(
        "Account,Oct 2025,Nov 2025\n"
        "Income,,\n"
        "Rooms Revenue,\"100,000.00\",\"120,000.00\"\n"
        "F&B Revenue,5000,\n"
        "Total Income,105000,120000\n"
        "Operating Expenses,,\n"
        "Salaries,40000,41000\n"
        "Duties & Taxes,,\n"
        "VAT payable,700,800\n"
        "Gross Profit,60000,79000\n"
    )
    assert list(stage.month_columns(t.headers).values()) == [stage.tf.parse_month("2025-10"),
                                                              stage.tf.parse_month("2025-11")]
    mp, tr = aliases.resolve(t.headers, "PL_STATEMENT", "pl_statement")
    rows = stage.stage_table(t, "PL_STATEMENT", mp, tr, currency="AED")
    ok = [(r.canonical["name"], r.canonical["period_month"], r.canonical["amount_minor"], r.canonical["section"])
          for r in rows if r.status == "OK"]
    assert ok == [
        ("Rooms Revenue", "2025-10-01", 10000000, "income"), ("Rooms Revenue", "2025-11-01", 12000000, "income"),
        ("F&B Revenue", "2025-10-01", 500000, "income"),
        ("Salaries", "2025-10-01", -4000000, "expense"), ("Salaries", "2025-11-01", -4100000, "expense"),
    ]
    skipped = [m for r in rows if r.status == "SKIP" for m in r.messages]
    assert "no amount this month" in skipped and "computed line" in skipped
    assert "duties and taxes are not P&L lines" in skipped
    assert "Total Income" not in [r.canonical.get("name") for r in rows]


def test_pl_single_amount_column_takes_the_upload_month():
    t = _table("Account,Amount\nRevenue,,\nRooms,1000\n")
    mp, tr = aliases.resolve(t.headers, "PL_STATEMENT", "pl_statement")
    rows = stage.stage_table(t, "PL_STATEMENT", mp, tr, currency="AED", period_month=stage.date(2025, 9, 1))
    assert rows[-1].canonical == {"name": "Rooms", "period_month": "2025-09-01", "amount_minor": 100000,
                                  "currency": "AED", "section": "income"}


# ─── AI samples ───


def test_ai_samples_mask_names_and_contacts_but_not_account_names():
    rows = [{"Conf": "1", "Guest": "Jane Doe", "E-mail": "jane@example.com",
             "Remarks": "call +971 50 123 4567 after 6", "Amount": "100"}] * 8
    out = mapping.mask_samples("PMS_RESERVATIONS", ["Conf", "Guest", "E-mail", "Remarks", "Amount"], rows)
    assert len(out) == mapping.SAMPLE_ROWS
    assert out[0] == {"Conf": "1", "Guest": "<masked>", "E-mail": "<masked>", "Remarks": "call <masked> after 6",
                      "Amount": "100"}
    fin = mapping.mask_samples("PL_STATEMENT", ["Account name", "Oct 2025"], [{"Account name": "Salaries",
                                                                             "Oct 2025": "1"}])
    assert fin == [{"Account name": "Salaries", "Oct 2025": "1"}]
    mapped = mapping.mask_samples("OTA_STATEMENT", ["Pax"], [{"Pax": "Jane Doe"}], mapping={"guest_name": "Pax"})
    assert mapped == [{"Pax": "<masked>"}]
