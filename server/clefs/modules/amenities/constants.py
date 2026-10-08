"""
Amenities constants: enums shared by the migrations, the catalog, the ledger
and the analytics, plus the thresholds of 02 spec 5.2 in one place so a test
can name them.
"""

from __future__ import annotations

from decimal import Decimal

# ─── Enums (mirror the CHECK constraints in 0500/0501) ───

CATEGORIES = ("BATH", "DENTAL", "SHAVING", "SLIPPERS", "WATER", "TEA_COFFEE", "MINIBAR", "OTHER")
BASES = ("PER_OCCUPIED_ROOM_NIGHT", "PER_ARRIVAL", "PER_GUEST_NIGHT", "ON_REQUEST")
EXCISE = ("NONE", "SWEETENED", "ENERGY")
LOCATION_KINDS = ("STORE", "FLOOR_CLOSET", "CART", "DEPARTMENT", "MINIBAR")
SHIFTS = ("MORNING", "EVENING", "NIGHT")
REASONS = ("REPLENISH", "GUEST_REQUEST", "VIP", "DAMAGE", "RETURN")
SOURCES = ("MANUAL", "CSV", "API", "DEMO")
MATCH_METHODS = ("TOOL", "ALIAS", "LLM", "MANUAL")
COST_SOURCES = ("SUPPLIER_INVOICE", "MANUAL", "SEED", "DEMO")

# VIP and damage lines are explained use: they never count as leakage.
EXCLUDED_REASONS = ("VIP", "DAMAGE")

# A stronger match replaces a weaker one on the same (request, item), never
# the reverse: a person's correction outranks the tool, the tool outranks text.
MATCH_RANK = {"LLM": 1, "ALIAS": 2, "TOOL": 3, "MANUAL": 4}

# ─── Variance and spike rule (02 spec 5.2) ───

WINDOW_DAYS = 7                     # floor closets hold 1-2 days, so alerts use 7-day sums
HISTORY_WINDOWS = 8                 # mu and sigma over the previous 8 weekly windows
MIN_HISTORY_WINDOWS = 4             # fewer and z cannot be judged: no alert
SPIKE_RATIO = Decimal("0.15")       # V7 / E7 > 15%
SPIKE_MIN_MAJOR = Decimal("100")    # V7 money > AED 100 (hotel currency major units)
SPIKE_Z = Decimal("2")              # z = (V7 - mu_8w) / sigma_8w > 2
SIGMA_FLOOR_UNITS = Decimal("1")    # a perfectly flat history still needs a finite z
REGRESSION_DAYS = 56                # issued ~ a + b * occupied over 8 weeks

# ─── Investigation verifier (02 spec 6.3) ───
# Verified when the next 14 days for the item and the flagged floor are below
# the spike threshold (V/E <= 15% or V money <= AED 100). The money bar is not
# doubled for the 14-day window on purpose: verified profit errs strict.

VERIFY_DAYS = 14                    # the window after the task is done
VERIFY_MIN_COVERED_DAYS = 8         # 4 occupancy days per week, as the spike rule asks

# ─── On-request rates (shrinkage toward a category prior) ───

RATE_N0 = 200                       # pseudo room-nights of prior weight
RATE_WINDOW_DAYS = 90
# Per 100 occupied room-nights. Dental, shaving and slippers are the spec's
# examples; the others are Clefs estimates the hotel's own logs replace fast.
RATE_PRIORS = {
    "DENTAL": Decimal("6"), "SHAVING": Decimal("3"), "SLIPPERS": Decimal("10"), "BATH": Decimal("5"),
    "WATER": Decimal("15"), "TEA_COFFEE": Decimal("8"), "MINIBAR": Decimal("10"), "OTHER": Decimal("2"),
}

# ─── Logging gap ───

GAP_DAYS = 14
GAP_RATIO = Decimal("1.5")          # issued on request > 1.5 x logged requests
GAP_MIN_ISSUED = Decimal("10")      # below this, a gap is noise

# ─── Par ───

PAR_Z = Decimal("1.65")             # safety = 1.65 x sigma_daily x sqrt(lead_days)
PAR_HISTORY_DAYS = 28
PAR_DEFAULTS = {                    # kind -> (days_of_supply, lead_days)
    "STORE": (Decimal("14"), Decimal("7")),
    "FLOOR_CLOSET": (Decimal("2"), Decimal("1")),
    "CART": (Decimal("1"), Decimal("1")),
    "DEPARTMENT": (Decimal("7"), Decimal("3")),
    "MINIBAR": (Decimal("7"), Decimal("1")),
}

# ─── Staff views ───

STAFF_MIN_DAYS = 14
STAFF_MIN_PEOPLE = 3
COUNTER_ROTATION_SHARE = Decimal("0.60")

# ─── Minibar ───

MINIBAR_MIN_UNBILLED_MAJOR = Decimal("100")
MINIBAR_MIN_UNBILLED_SHARE = Decimal("0.10")

# ─── Benchmarks ───

BENCH_MIN_HOTELS = 3
BENCH_EXACT_FROM = 5                # below this, bands only
BENCH_MAX_SHARE = Decimal("0.50")
