"""
UAE OTA Leakage Index (Canon 23, Canon K5 claim `uae_ota_leakage_index`;
00-PLAN Profit Guard "Public audit").

Built only from public audits whose uploader ticked the separate index
consent at unlock. One anonymised row per audit goes to pfg_index_samples:
the OTA, the emirate and property band (the audit's defaults unless the
lead said otherwise), the statement's month, line count, commission,
the conservative leak amount and the per-detector counts. No hotel name, no
guest data, no contact detail, no token: the sample outlives the 30-day
purge of the audit it came from (audit_id becomes NULL).

The aggregate is published only when n >= MIN_N (30) samples exist, and
then always with its methodology. Below that the public route says only
that the index is not published yet and how many more samples it needs;
no figure leaks out of a small sample. A per-OTA row follows the same
rule on its own n.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

from clefs import db
from clefs.core import clock

CLAIM_ID = "uae_ota_leakage_index"
MIN_N = 30
TITLE = "UAE OTA Leakage Index"

# ─── Methodology (published with every answer) ───

METHODOLOGY = {
    "source": "OTA statements uploaded to the free Clefs OTA audit by hotels in the UAE whose uploader "
              "consented to an anonymised contribution. One sample per statement; the same file uploaded "
              "twice counts once.",
    "measure": "Leak rate = the amount of one statement's counted findings (commission on no-shows and "
               "cancellations, and refunds without the commission reduced) divided by the commission it "
               "charges. Commission on municipality fee and Tourism Dirham, and a percent above the default, "
               "are lines to confirm with the hotel and are not counted in the index.",
    "detectors": "Statement-only checks: commission on no-shows and cancellations by the statement's own "
                 "status (L3), refunds without the commission reduced (L12). A percent above the default (L2) is "
                 "not part of the index: the contract or a programme can explain it. Checks that need PMS, "
                 "payout, card or bank data are not part of the index.",
    "assumptions": "A default percentage per OTA, not a market rate (Booking.com 15%, Agoda 18%, Expedia 18%), "
                   "used only for a refund whose booking has no commissioned line in the statement.",
    "statistic": "Median and interquartile range of the leak rate, and the share of statements with at "
                 "least one finding. Amounts are not averaged across currencies.",
    "publication": f"Published only when at least {MIN_N} samples exist; a per-OTA row only when that OTA has "
                   f"{MIN_N} samples of its own.",
    "limitations": "Self-selected sample (hotels that chose to run an audit); a finding is an estimate the OTA "
                   "may dispute; default terms can differ from a property's real contract.",
}

# ─── Samples ───

# Only what the audit's own headline counts (public_audit "Public classes"):
# L2 left on 2026-10-06, since a percent above a default per OTA can be the
# contract or a programme (CHECK_CONTRACT), not a leak.
INDEX_DETECTORS = ("L3", "L12")


def _rate(leak: int, commission: int) -> Decimal | None:
    if commission <= 0:
        return None
    return (Decimal(leak) / Decimal(commission)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def record_sample(cur: Any, audit: dict, report: dict, *, emirate: str | None = None,
                  property_band: str | None = None) -> int | None:
    """Add the audit's anonymised sample once (called at unlock when the
    index consent is ticked). Skips an audit that already has one, and a
    file another consented audit already contributed (same staged batch).
    Returns the sample id, or None when nothing was added."""
    stats = (report or {}).get("index") or {}
    if not stats.get("commission_minor"):
        return None  # nothing charged: no rate to contribute
    cur.execute("SELECT 1 FROM pfg_index_samples WHERE audit_id = %s", (audit["id"],))
    if cur.fetchone() is not None:
        return None
    if audit.get("batch_id") is not None:
        cur.execute(
            """
            SELECT 1 FROM pfg_index_samples s JOIN pfg_lead_audits a ON a.id = s.audit_id
             WHERE a.batch_id = %s AND a.id <> %s LIMIT 1
            """,
            (audit["batch_id"], audit["id"]),
        )
        if cur.fetchone() is not None:
            return None
    leak, commission = int(stats.get("leak_minor") or 0), int(stats["commission_minor"])
    cur.execute(
        """
        INSERT INTO pfg_index_samples (audit_id, created_at, ota_code, emirate, property_band, period_month, lines,
            commission_minor, leak_minor, leak_rate, currency, detector_counts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (audit["id"], clock.now(), stats.get("ota_code") or "UNKNOWN", emirate or stats.get("emirate"),
         property_band or stats.get("property_band"), stats.get("period_month"), int(stats.get("lines") or 0),
         commission, leak, _rate(leak, commission), stats.get("currency") or "AED",
         db.Json(stats.get("detector_counts") or {})),
    )
    return int(cur.fetchone()["id"])


# ─── Aggregate ───


def _q(x: Decimal | float) -> str:
    return str(Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _quartiles(xs: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """(p25, median, p75) by the median-of-halves rule (small, stable, no
    interpolation choices to explain)."""
    s = sorted(xs)
    n = len(s)
    lo, hi = s[: n // 2], s[(n + 1) // 2:]
    return Decimal(str(median(lo or s))), Decimal(str(median(s))), Decimal(str(median(hi or s)))


def _stats(rows: list[dict]) -> dict:
    rates = [Decimal(str(r["leak_rate"])) for r in rows if r["leak_rate"] is not None]
    p25, p50, p75 = _quartiles(rates) if rates else (Decimal(0),) * 3
    with_leak = sum(1 for r in rows if int(r["leak_minor"] or 0) > 0)
    return {"n": len(rows), "median_leak_rate": _q(p50), "p25_leak_rate": _q(p25), "p75_leak_rate": _q(p75),
            "share_with_leak": _q(Decimal(with_leak) / Decimal(len(rows))) if rows else "0.0000"}


def _current(r: dict) -> dict:
    """One stored sample under today's INDEX_DETECTORS. A sample recorded
    before 2026-10-06 counted L2 in leak_minor, leak_rate and
    detector_counts, and it outlives the 30-day purge, so the published
    median, IQR, share_with_leak and detector_share would mix the two
    measures. The leak is re-summed from the per-detector minors instead
    (leak_minor was exactly their sum when recorded), never trusted as
    stored."""
    dc = {d: v for d, v in (r["detector_counts"] or {}).items() if d in INDEX_DETECTORS}
    leak = sum(int(v.get("minor") or 0) for v in dc.values() if isinstance(v, dict))
    return {**r, "detector_counts": dc, "leak_minor": leak, "leak_rate": _rate(leak, int(r["commission_minor"]))}


def aggregate(cur: Any) -> dict:
    """The public answer. Below MIN_N: {published: false, n, min_n,
    needed, methodology}, with no figure. From MIN_N: the leak-rate median
    and IQR, the share of statements with a finding, the detector
    frequencies and the per-OTA rows that clear MIN_N on their own."""
    cur.execute("SELECT ota_code, commission_minor, leak_minor, leak_rate, detector_counts, period_month, "
                "created_at FROM pfg_index_samples ORDER BY id")
    rows = [_current(dict(r)) for r in cur.fetchall()]
    n = len(rows)
    base = {"title": TITLE, "claim_id": CLAIM_ID, "min_n": MIN_N, "n": n, "methodology": METHODOLOGY}
    if n < MIN_N:
        return {**base, "published": False, "needed": MIN_N - n}
    by_ota: dict[str, list[dict]] = {}
    for r in rows:
        by_ota.setdefault(r["ota_code"], []).append(r)
    freq: dict[str, int] = {}
    for r in rows:
        for det, v in (r["detector_counts"] or {}).items():
            count = int(v.get("n") or 0) if isinstance(v, dict) else int(v or 0)
            if count > 0:
                freq[det] = freq.get(det, 0) + 1
    months = [r["period_month"] for r in rows if r["period_month"] is not None]
    return {
        **base, "published": True, "as_of": clock.now().isoformat(),
        "period": {"from": min(months).isoformat(), "to": max(months).isoformat()} if months else None,
        **_stats(rows),
        "detector_share": {d: _q(Decimal(c) / Decimal(n)) for d, c in sorted(freq.items())},
        "by_ota": [{"ota_code": ota, **_stats(rs)} for ota, rs in sorted(by_ota.items()) if len(rs) >= MIN_N],
        "suppressed_otas": sorted(ota for ota, rs in by_ota.items() if len(rs) < MIN_N),
    }
