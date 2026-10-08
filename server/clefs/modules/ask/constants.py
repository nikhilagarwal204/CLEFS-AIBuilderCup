"""
Ask Clefs limits and vocabularies (02 spec 7). One place, so the tool
schemas, the validators, the README and the tests agree on every number.
"""

from __future__ import annotations

# ─── Question budget ───

MAX_TOOL_CALLS = 3  # per question, across every planning round (spec 7)
MAX_QUERIES = 3  # query_metric calls whose results one answer may cite
QUESTIONS_PER_HOUR = 30  # per user, all hotels and channels together
RATE_WINDOW_S = 3600
MAX_QUESTION_CHARS = 500
MAX_DIMENSIONS = 2
MAX_FILTERS = 5
MAX_FILTER_VALUES = 20
MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MAX_SPAN_DAYS = 3 * 366 + 1  # the registry's own ceiling (core.metrics.MAX_SPAN_DAYS)

# ─── Periods ───
# The spec's presets first; the extra ones cover how owners actually ask
# ("this week", "last 30 days") without letting the model invent dates.
PERIOD_PRESETS = (
    "today", "yesterday", "last_7d", "mtd", "last_month", "qtd", "ytd", "next_30d", "next_90d",
    "wtd", "last_week", "last_30d", "last_quarter", "last_year", "next_7d", "next_14d",
)
COMPARE_TO = ("none", "previous_period", "same_period_last_year")
COMPARE_MAP = {"none": None, "previous_period": "prev_period", "same_period_last_year": "prev_year"}
GRAINS = ("total", "day", "week", "month")
FILTER_OPS = ("eq", "in", "gte", "lte")
SORTS = ("value_desc", "value_asc", "label_asc", "time_asc")

# ─── Channels, statuses, roles ───

CHANNELS = ("WEB", "WHATSAPP", "VIEW")
STATUSES = ("answered", "table_only", "unsupported", "refused", "error")
# WhatsApp Q&A is for the people who decide money (spec 7): never dept heads
# or front desk, whatever permissions a hotel grants them.
WA_ROLES = frozenset({"owner", "gm", "finance"})
WA_MAX_ROWS = 10
WA_MAGIC_TTL_H = 24

# ─── Guardrails ───
# Dimension keys that would expose one guest. A module may register them for
# its own screens; Ask never offers them to the model or accepts them back.
PII_DIMENSIONS = frozenset({
    "guest", "guest_name", "guest_id", "guest_ref", "name", "first_name", "last_name", "email", "phone",
    "mobile", "contact", "passport", "id_number", "reservation", "reservation_id", "confirmation",
    "confirmation_no", "booking_ref", "address", "card", "card_number",
})

# AI tasks (ai_calls.task; model via AI_MODEL_ASK_PLAN / AI_MODEL_ASK_ANSWER).
TASK_PLAN = "ask.plan"
TASK_ANSWER = "ask.answer"

# Filter-value cache: the distinct values a dimension may take at a hotel.
VALUES_TTL_S = 600
