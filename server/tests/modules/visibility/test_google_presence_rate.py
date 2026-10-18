"""The Google tab's "Official price shown (30 days)" counts stay-date checks
(QA OWN-16): one checked day with the official price on one stay date and
missing on another reads 50%, not the 100% a per-day BOOL_OR gave."""

from __future__ import annotations

from tests.modules.visibility.test_google_hotels import (  # noqa: F401  (fixtures)
    _details, _router, env, evidence_calls, hotel, serp,
)


def test_presence_rate_counts_stay_date_checks(cur, hotel, serp, evidence_calls):  # noqa: F811
    from clefs.modules.visibility import google_hotels

    serp.default = None
    serp.queue = [_router({"2026-10-02": _details(450, [("Booking.com", 452)]),
                           "2026-10-29": _details(None, [("Booking.com", 396)])})] * 6
    assert google_hotels.run_daily(cur, hotel)["observations"] == 2

    view = google_hotels.overview(cur, hotel)
    pres = view["presence"]
    assert (pres["rate_30d"], pres["shown_30d"], pres["checks_30d"], pres["days_30d"]) == ("50.0", 1, 2, 1)
    # the table below it shows the same two stays, one without an official price
    assert sorted(r["official_present"] for r in view["parity"]) == [False, True]
