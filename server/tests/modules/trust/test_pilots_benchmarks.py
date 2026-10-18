"""Anonymised benchmark cohorts: opt-in only, n >= 3, bands only below 5 (03 spec 5.5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from clefs.core import hotels
from clefs.modules.trust.pilots import benchmarks
from tests.modules.trust.test_pilots import verified_win, win_type  # noqa: F401  (fixture)

DUBAI = timezone(timedelta(hours=4))
AUG = datetime(2026, 8, 12, 12, tzinfo=DUBAI)


def _hotels(cur, n, clock, *, base="bench"):
    slugs = []
    for i in range(n):
        slug = f"{base}-{i}"
        hotels.create_hotel(cur, slug=slug, name=f"Bench {i}", city="Dubai")
        cur.execute("INSERT INTO trs_benchmark_optins (hotel_slug, opted_in) VALUES (%s, TRUE)", (slug,))
        verified_win(cur, slug, (i + 1) * 100000, AUG, clock)
        slugs.append(slug)
    return slugs


def _cohort(cur):
    return benchmarks.upsert_cohort(cur, None, benchmarks.CohortIn(key="dubai-all", label="Dubai hotels",
                                                                   criteria={"country": "AE", "emirate": "dubai"},
                                                                   metrics=["core.verified_value"]))


def test_assign_bands_ranks_ties_and_direction():
    vals = {"a": Decimal(1), "b": Decimal(2), "c": Decimal(3)}
    assert benchmarks.assign_bands(vals) == {"c": "top", "b": "middle", "a": "bottom"}
    assert benchmarks.assign_bands(vals, higher_is_better=False) == {"a": "top", "b": "middle", "c": "bottom"}
    tied = benchmarks.assign_bands({"a": Decimal(5), "b": Decimal(5), "c": Decimal(1)})
    assert tied["a"] == tied["b"] == "top" and tied["c"] == "bottom"


def test_three_hotels_get_bands_and_never_a_median(cur, win_type, clock):  # noqa: F811
    slugs = _hotels(cur, 3, clock)
    cohort = _cohort(cur)
    out = benchmarks.snapshot(cur, cohort, "core.verified_value", "2026-08")
    assert out == {"cohort": "dubai-all", "metric_key": "core.verified_value", "period": "2026-08", "n_hotels": 3,
                   "published": True}
    cur.execute("SELECT p50, bands FROM trs_benchmark_snapshots WHERE cohort_id = %s", (cohort["id"],))
    row = cur.fetchone()
    assert row["p50"] is None and row["bands"] == {}
    view = benchmarks.for_hotel(cur, slugs[2], period="2026-08")
    item = view["items"][0]
    assert item["band"] == "top" and item["n_hotels"] == 3 and item["p50"] is None and item["bands_only"]
    assert benchmarks.for_hotel(cur, slugs[0])["items"][0]["band"] == "bottom"
    # No other hotel's value or slug reaches the view.
    assert "bench-1" not in str(view) and "1000" not in str(view)


def test_fewer_than_three_hotels_publish_nothing(cur, win_type, clock):  # noqa: F811
    slugs = _hotels(cur, 3, clock)
    cohort = _cohort(cur)
    benchmarks.snapshot(cur, cohort, "core.verified_value", "2026-08")
    benchmarks.set_opt_in(cur, None, slugs[1], False)
    out = benchmarks.snapshot(cur, cohort, "core.verified_value", "2026-08")
    assert out["published"] is False and out["n_hotels"] == 2
    assert benchmarks.for_hotel(cur, slugs[0])["items"] == []
    assert benchmarks.for_hotel(cur, slugs[1]) == {"opted_in": False, "items": []}


def test_five_hotels_show_the_median(cur, win_type, clock):  # noqa: F811
    slugs = _hotels(cur, 5, clock)
    cohort = _cohort(cur)
    benchmarks.snapshot(cur, cohort, "core.verified_value", "2026-08")
    item = benchmarks.for_hotel(cur, slugs[0], period="2026-08")["items"][0]
    assert Decimal(item["p50"]) == Decimal("3000") and item["bands_only"] is False and len(item["cutoffs"]) == 2


def test_hotels_outside_the_criteria_or_not_opted_in_are_left_out(cur, win_type, clock):  # noqa: F811
    _hotels(cur, 3, clock)
    hotels.create_hotel(cur, slug="abu-1", name="Abu", city="Abu Dhabi", emirate="abu_dhabi")
    cur.execute("INSERT INTO trs_benchmark_optins (hotel_slug, opted_in) VALUES ('abu-1', TRUE)")
    hotels.create_hotel(cur, slug="shy-1", name="Shy", city="Dubai")
    assert sorted(benchmarks.members(cur, _cohort(cur))) == ["bench-0", "bench-1", "bench-2"]


def test_benchmark_routes(cur, hotel_uae, client_as, win_type, clock):  # noqa: F811
    slug = hotel_uae.slug
    assert client_as("gm").put(f"/hotels/{slug}/trust/benchmarks/opt-in", json={"opted_in": True}).status_code == 403
    owner = client_as("owner")
    assert owner.put(f"/hotels/{slug}/trust/benchmarks/opt-in", json={"opted_in": True}).json()["opted_in"] is True
    assert owner.get(f"/hotels/{slug}/trust/benchmarks").json() == {"opted_in": True, "items": []}
    body = {"key": "dubai-all", "label": "Dubai", "criteria": {"emirate": "dubai"}, "metrics": ["core.verified_value"]}
    assert owner.put(f"/hotels/{slug}/trust/benchmarks/cohorts", json=body).status_code == 403
    admin = client_as("owner", admin=True)
    assert admin.put(f"/hotels/{slug}/trust/benchmarks/cohorts", json=body).status_code == 200
    bad = admin.put(f"/hotels/{slug}/trust/benchmarks/cohorts", json={**body, "criteria": {"planet": "mars"}})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_criteria"
    snaps = admin.post(f"/hotels/{slug}/trust/benchmarks/snapshots", json={"period": "2026-08"}).json()
    assert snaps[0]["published"] is False  # one opted-in hotel is not a cohort
