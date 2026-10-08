"""Statistical inputs in the hotel's currency (review.compute_stat_inputs).

Regression: Profit Guard's channel economics returns one row per (channel,
currency) in the reservation's own currency, and the PMS rooms revenue and
expected OTA commission used to add those minor units raw (USD cents onto
AED fils). Each row now converts at the month end; a row with no rate is
left out and listed in fx_skipped, never summed raw.
"""

from __future__ import annotations

from datetime import date

from clefs.modules.finance import review

SEP = date(2026, 9, 1)


def _row(channel: str, ccy: str, is_ota: bool, rev: int, com: int, rn: int = 10) -> dict:
    return {"channel_code": channel, "currency": ccy, "is_ota": is_ota, "room_nights": rn,
            "room_rev_fils": rev, "commission_fils": com}


def test_channel_rows_convert_to_the_hotel_currency(cur, hotel_uae, neighbours):
    neighbours.chan = [
        _row("DIRECT", "AED", False, 1_000_000, 0),
        _row("BOOKING", "AED", True, 500_000, 75_000),
        _row("EXPEDIA", "USD", True, 10_000, 1_800),    # USD 100.00 / 18.00 at the 3.6725 peg
    ]
    v = review.compute_stat_inputs(cur, hotel_uae.slug, SEP)
    assert v["currency"] == "AED" and v["fx_skipped"] == []
    assert v["rooms_rev_pms_minor"] == 1_000_000 + 500_000 + 36_725
    assert v["ota_commission_expected_minor"] == 75_000 + 6_611   # 18 x 3.6725 = 66.105 -> 66.11
    assert str(v["ota_share"]) == "0.6667"                        # room nights: currency-free


def test_a_row_with_no_rate_is_left_out_and_listed(cur, hotel_uae, neighbours):
    neighbours.chan = [
        _row("DIRECT", "AED", False, 1_000_000, 0),
        _row("HOTELBEDS", "EUR", True, 20_000, 4_000),  # no EUR rate in fx_rates, no peg
    ]
    v = review.compute_stat_inputs(cur, hotel_uae.slug, SEP)
    assert v["rooms_rev_pms_minor"] == 1_000_000 and v["ota_commission_expected_minor"] == 0
    assert v["fx_skipped"] == [{"channel_code": "HOTELBEDS", "currency": "EUR", "room_rev_minor": 20_000,
                                "commission_minor": 4_000}]
    # stored, then read back with the skipped list
    stored = review.store_stat_inputs(cur, hotel_uae.slug, SEP)
    assert stored["source"] == "stored" and stored["rooms_rev_pms_minor"] == 1_000_000
    assert stored["fx_skipped"][0]["currency"] == "EUR"
    # check 2 tells the owner the PMS figure is partial
    batch = {"id": 0, "currency": "AED", "period_month": SEP, "statement_kind": "pl",
             "source_net_profit_minor": 0}
    pms = next(i for i in review.run_checks(cur, hotel_uae.slug, batch, lines=[])["items"] if i["key"] == "pms_rooms")
    assert "1 channel row(s) in EUR left out" in pms["detail"] and pms["data"]["fx_skipped"]


def test_every_row_skipped_means_no_pms_figure(cur, hotel_uae, neighbours):
    neighbours.chan = [_row("HOTELBEDS", "EUR", True, 20_000, 4_000)]
    v = review.compute_stat_inputs(cur, hotel_uae.slug, SEP)
    assert v["rooms_rev_pms_minor"] is None and v["ota_commission_expected_minor"] is None
    assert len(v["fx_skipped"]) == 1
