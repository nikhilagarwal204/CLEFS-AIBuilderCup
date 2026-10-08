"""
0674_guests_wa_rate_card: Meta's per-message WhatsApp rates (04 spec 3.2 and
3.10, assumption A4). Intentionally global: a rate belongs to the
recipient's market, not to a hotel, so there is no hotel_slug column.

Every row is UNVERIFIED and carries its source_url. Meta publishes exact
rates only in its rate-card CSV/PDF; these are the indicative figures from
docs/specs/research/uae/whatsapp.md section 3. Rates are vendor costs, so
they are stored in micro-units of their currency (Canon K3: *_micros BIGINT
plus a currency), never rounded to fils.

`service` is the category Meta starts charging on 2026-10-01 for free-form
messages and utility templates inside the customer service window, at the
market's utility rate (less WA_SERVICE_FREE_ALLOWANCE, which defaults to 0).
Idempotent: rows are keyed (market, category, effective_from).
"""

DDL = """
CREATE TABLE IF NOT EXISTS gst_wa_rate_card (
    market          CHAR(2) NOT NULL,
    category        TEXT NOT NULL CHECK (category IN ('marketing', 'utility', 'authentication', 'service')),
    effective_from  DATE NOT NULL,
    rate_micros     BIGINT NOT NULL CHECK (rate_micros >= 0),
    currency        CHAR(3) NOT NULL,
    source_url      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (status IN ('UNVERIFIED', 'VERIFIED')),
    PRIMARY KEY (market, category, effective_from)
)
"""

_UAE_SRC = "https://adjoltz.com/uae-whatsapp-rate-card"
_UAE_META = "https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing"
_UAE_SERVICE = "https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages"
_IN_SRC = "https://whautomate.com/whatsapp-business-api-pricing-india"

# (market, category, effective_from, rate_micros, currency, source_url)
ROWS = (
    # UAE, USD. The older marketing figure was $0.0385; Meta confirmed an
    # increase on 2025-10-01 (reported at about $0.05).
    ("AE", "marketing", "2025-07-01", 38_500, "USD", _UAE_SRC),
    ("AE", "marketing", "2025-10-01", 50_000, "USD", _UAE_META),
    ("AE", "utility", "2025-07-01", 16_000, "USD", _UAE_SRC),
    ("AE", "authentication", "2025-07-01", 18_000, "USD", _UAE_SRC),
    ("AE", "service", "2026-10-01", 16_000, "USD", _UAE_SERVICE),
    # India, INR before 18% GST (the legacy pilots).
    ("IN", "marketing", "2026-01-01", 863_100, "INR", _IN_SRC),
    ("IN", "utility", "2026-01-01", 115_000, "INR", _IN_SRC),
    ("IN", "authentication", "2026-01-01", 115_000, "INR", _IN_SRC),
    ("IN", "service", "2026-10-01", 115_000, "INR", _UAE_SERVICE),
)


def up(cur):
    cur.execute(DDL)
    for market, category, effective_from, rate, currency, url in ROWS:
        cur.execute(
            """
            INSERT INTO gst_wa_rate_card (market, category, effective_from, rate_micros, currency, source_url, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'UNVERIFIED')
            ON CONFLICT (market, category, effective_from) DO NOTHING
            """,
            (market, category, effective_from, rate, currency, url),
        )
