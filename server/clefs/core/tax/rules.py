"""Date-effective fee rules, owner overrides and the hotel's tax profile.

Resolution for one hotel and stay date (02 spec 2.3; 04 spec D3):
1. Candidates: rules of the hotel's emirate (seeded, hotel_slug NULL) plus the
   hotel's own OWNER_CONFIGURED rules, whose window holds the date and whose
   property_category is '*' or the hotel's category.
2. An ANNOUNCED_UNVERIFIED rule counts only while the hotel has an override
   with apply = TRUE (the owner toggle), inside the override's own window when
   it narrows one. An override with apply = FALSE switches any rule off.
3. Per fee code the winner is: an applied unverified rule (the owner opted in
   to a more specific announcement, such as the 2026 exemption), then the
   hotel's own rule, then a category-specific rule, then the newest.

A HotelTaxContext loads every rule for a date span once, so compose_stay
resolves each night in memory instead of one query per night.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from clefs.core.money import D, ccy
from clefs.core.tax.models import EMIRATE_CODES, PROPERTY_CATEGORIES, PercentFee, TaxProfile

UNVERIFIED = "ANNOUNCED_UNVERIFIED"

# ─── Hotel facts ───


def emirate_code(emirate: str | None) -> str | None:
    """hotels.emirate word ('dubai') or a code ('DXB') -> the fee_rules code."""
    if not emirate:
        return None
    e = str(emirate).strip()
    if e.upper() in EMIRATE_CODES.values():
        return e.upper()
    return EMIRATE_CODES.get(e.lower().replace(" ", "_").replace("-", "_"))


def derive_category(category: str | None, star_rating: int | None) -> str | None:
    """A property category from the hotel row when no tax profile names one.
    Hotel apartments and holiday homes grade luxury/deluxe/standard by stars;
    a hotel without a star rating has no TD rule (a notice says so)."""
    text = (category or "").strip().lower()
    if text in PROPERTY_CATEGORIES:
        return text
    stars = int(star_rating) if star_rating else None
    if "apartment" in text:
        return "apartment_luxury" if (stars or 0) >= 5 else "apartment_deluxe" if stars == 4 else "apartment_standard"
    if "holiday" in text:
        return "holiday_home_luxury" if (stars or 0) >= 5 else "holiday_home_standard"
    if "guest" in text:
        return "guest_house"
    if stars is None:
        return None
    return f"hotel_{min(max(stars, 1), 5)}"


# ─── Context ───


@dataclass
class HotelTaxContext:
    """One hotel's tax inputs for a date span, loaded once."""

    hotel_slug: str
    emirate: str | None
    currency: str
    fallback_category: str | None
    fallback_convention: str
    profiles: list[dict] = field(default_factory=list)  # hotel_tax_profiles, newest first
    rules: list[dict] = field(default_factory=list)  # fee_rules + override columns

    # ─── Profile rows ───

    def profile_row(self, on: date) -> dict | None:
        return next((p for p in self.profiles if p["effective_from"] <= on), None)

    def category(self, on: date) -> str | None:
        row = self.profile_row(on)
        return (row or {}).get("property_category") or self.fallback_category

    def convention(self, on: date) -> str:
        row = self.profile_row(on)
        return ((row or {}).get("convention") or self.fallback_convention or "A").strip().upper()

    def td_per_bedroom(self, on: date) -> bool | None:
        row = self.profile_row(on)
        return None if row is None else row.get("td_per_bedroom")

    # ─── Rule selection ───

    def active(self, rule: dict, on: date, category: str | None) -> bool:
        """Whether `rule` applies to this hotel on `on` (steps 1 and 2)."""
        if rule["property_category"] not in ("*", category):
            return False
        applied = rule.get("ov_apply")
        if applied is False:
            return False
        if rule["rule_status"] == UNVERIFIED and applied is not True:
            return False
        start = rule.get("ov_from") or rule["effective_from"]
        end = rule.get("ov_to") or rule["effective_to"]
        # An override narrows, never widens, the announced window.
        start = max(start, rule["effective_from"])
        if rule["effective_to"] is not None:
            end = min(end, rule["effective_to"]) if end is not None else rule["effective_to"]
        return start <= on and (end is None or on <= end)

    @staticmethod
    def _rank(rule: dict) -> tuple:
        return (
            rule["rule_status"] == UNVERIFIED,
            rule["hotel_slug"] is not None,
            rule["property_category"] != "*",
            rule["effective_from"],
            rule["id"],
        )

    def winners(self, on: date, *, calc_modes: tuple[str, ...] = ("PERCENT", "PER_ROOM_NIGHT")) -> dict[str, dict]:
        """fee_code -> the winning rule on `on` (step 3)."""
        cat = self.category(on)
        best: dict[str, dict] = {}
        for r in self.rules:
            if r["calc_mode"] not in calc_modes or not self.active(r, on, cat):
                continue
            cur_best = best.get(r["fee_code"])
            if cur_best is None or self._rank(r) > self._rank(cur_best):
                best[r["fee_code"]] = r
        return best

    def profile(self, on: date) -> TaxProfile:
        """The TaxProfile for one stay date."""
        win = self.winners(on)
        fees = tuple(
            PercentFee(code, D(r["rate"]), tuple(r["base"] or ()) or ("ROOM",), r["id"], r["rule_status"])
            for code, r in win.items()
            if r["calc_mode"] == "PERCENT"
        )
        td = win.get("TD")
        td_ok = td is not None and td["calc_mode"] == "PER_ROOM_NIGHT"
        per_bedroom = self.td_per_bedroom(on)
        if per_bedroom is None:
            per_bedroom = bool(td["per_bedroom"]) if td_ok else False
        return TaxProfile(
            convention=self.convention(on),
            currency=self.currency,
            fees=fees,
            td_rate=D(td["rate"]) if td_ok else Decimal(0),
            td_cap_nights=td["cap_consecutive_nights"] if td_ok else None,
            td_per_bedroom=per_bedroom,
            td_rule_id=td["id"] if td_ok else None,
            td_rule_status=td["rule_status"] if td_ok else None,
            emirate=self.emirate,
            property_category=self.category(on),
            on=on,
        )

    # ─── Announcements and deferrals ───

    def unverified_intersecting(self, start: date, end: date) -> list[dict]:
        """Unverified rules whose announced window meets [start, end], applied
        or not: both deserve the banner (Canon: date-effective rules)."""
        out = []
        for r in self.rules:
            if r["rule_status"] != UNVERIFIED:
                continue
            if r["effective_from"] <= end and (r["effective_to"] is None or r["effective_to"] >= start):
                out.append(r)
        return out

    def notices(self, start: date, end: date) -> list[str]:
        """Banner lines for a stay or window, one per announcement group."""
        seen: set[str] = set()
        out: list[str] = []
        for r in sorted(self.unverified_intersecting(start, end), key=lambda r: r["id"]):
            group = (r.get("params") or {}).get("group") or r["rule_key"] or str(r["id"])
            if group in seen:
                continue
            seen.add(group)
            state = "applied at the owner's request" if r.get("ov_apply") is True else "not applied"
            out.append(f"{r['label']}: announced, not yet verified ({state}). Source: {r['source_url'] or 'owner'}")
        if self.emirate is None:
            out.append("The hotel has no emirate set, so no UAE fee rules apply.")
        return out

    def deferral_months(self, fee_code: str, month: date) -> int:
        """Remittance deferral months that apply to fees collected in `month`
        (the first day of the collection month decides)."""
        months = 0
        for r in self.rules:
            if r["calc_mode"] != "REMIT_DEFERRAL" or r["fee_code"] != fee_code:
                continue
            if self.active(r, month, self.category(month)):
                months = max(months, int((r.get("params") or {}).get("remit_deferral_months") or 0))
        return months


# ─── Loading ───

_RULE_COLUMNS = """
    r.id, r.rule_key, r.hotel_slug, r.emirate, r.property_category, r.fee_code, r.calc_mode, r.rate,
    r.currency, r.base, r.vat_applies, r.cap_consecutive_nights, r.per_bedroom, r.effective_from,
    r.effective_to, r.rule_status, r.label, r.params, r.source_url, r.verified_by, r.verified_at,
    o.apply AS ov_apply, o.effective_from AS ov_from, o.effective_to AS ov_to, o.note AS ov_note,
    o.acknowledged_by AS ov_acknowledged_by, o.acknowledged_at AS ov_acknowledged_at
"""


def _load_rules(cur: Any, hotel_slug: str, emirate: str | None, start: date | None, end: date | None) -> list[dict]:
    cur.execute(
        f"""
        SELECT {_RULE_COLUMNS}
        FROM fee_rules r
        LEFT JOIN hotel_tax_overrides o ON o.fee_rule_id = r.id AND o.hotel_slug = %(slug)s
        WHERE (r.hotel_slug = %(slug)s OR (r.hotel_slug IS NULL AND r.emirate = %(em)s))
          AND (%(end)s::date IS NULL OR r.effective_from <= %(end)s::date)
          AND (%(start)s::date IS NULL OR r.effective_to IS NULL OR r.effective_to >= %(start)s::date)
        ORDER BY r.emirate, r.fee_code, r.property_category, r.effective_from, r.id
        """,
        {"slug": hotel_slug, "em": emirate, "start": start, "end": end},
    )
    return [dict(r) for r in cur.fetchall()]


def load_context(cur: Any, hotel_slug: str, start: date | None = None, end: date | None = None) -> HotelTaxContext:
    """Every tax input of one hotel whose window meets [start, end] (None =
    unbounded). hotels.tax_profile JSON ({"convention", "property_category"})
    is the fallback for hotels without a hotel_tax_profiles row."""
    from clefs.core.hotels import get_hotel

    h = get_hotel(cur, hotel_slug)
    # UAE rules only for a UAE hotel: a legacy Indian hotel keeps no fees.
    em = emirate_code(h.emirate) if (h.country or "AE").upper() == "AE" else None
    tp = h.tax_profile or {}
    cur.execute(
        "SELECT * FROM hotel_tax_profiles WHERE hotel_slug = %s ORDER BY effective_from DESC",
        (hotel_slug,),
    )
    profiles = [dict(r) for r in cur.fetchall()]
    return HotelTaxContext(
        hotel_slug=hotel_slug,
        emirate=em,
        currency=ccy(h.currency or "AED"),
        fallback_category=derive_category(tp.get("property_category") or h.category, h.star_rating),
        fallback_convention=str(tp.get("convention") or "A").upper(),
        profiles=profiles,
        rules=_load_rules(cur, hotel_slug, em, start, end),
    )


def resolve_profile(cur: Any, hotel_slug: str, on: date) -> TaxProfile:
    """The hotel's TaxProfile for one date, with that date's notices."""
    ctx = load_context(cur, hotel_slug, on, on)
    p = ctx.profile(on)
    return TaxProfile(**{**p.__dict__, "notices": tuple(ctx.notices(on, on))})


def list_fee_rules(cur: Any, hotel_slug: str, *, on: date | None = None) -> list[dict]:
    """Every rule that can apply to the hotel (seeded for its emirate plus its
    own), each with its override and, for `on` (default: none), whether it
    applies that day. Serializable: dates ISO, rates as strings."""
    ctx = load_context(cur, hotel_slug)
    cat = ctx.category(on) if on else None
    out = []
    for r in ctx.rules:
        row = {k: v for k, v in r.items() if not k.startswith("ov_")}
        row["rate"] = str(D(r["rate"]).normalize())
        row["override"] = (
            None
            if r["ov_apply"] is None
            else {"apply": r["ov_apply"], "effective_from": r["ov_from"], "effective_to": r["ov_to"], "note": r["ov_note"]}
        )
        if on is not None:
            row["applies_on"] = ctx.active(r, on, cat)
        out.append(row)
    return out


# ─── Writes ───


def _user_id(ctx: Any) -> int | None:
    principal = getattr(ctx, "principal", ctx)
    return getattr(principal, "user_id", None)


def _audit(cur: Any, ctx: Any, hotel_slug: str, action: str, entity: str, entity_id: Any, **kw: Any) -> None:
    if ctx is None:
        return
    from clefs.audit import audit

    audit(cur, ctx, action, entity, entity_id, hotel_slug=hotel_slug, **kw)


def _rule_id(cur: Any, rule: int | str) -> int:
    """A fee rule id from an id or a seeded rule_key."""
    from clefs.core.errors import ClefsError

    if isinstance(rule, int) or (isinstance(rule, str) and rule.isdigit()):
        cur.execute("SELECT id FROM fee_rules WHERE id = %s", (int(rule),))
    else:
        cur.execute("SELECT id FROM fee_rules WHERE rule_key = %s", (rule,))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, f"Fee rule {rule} not found", "fee_rule_not_found")
    return int(row["id"])


def set_override(
    cur: Any,
    hotel_slug: str,
    rule: int | str,
    *,
    apply: bool,
    effective_from: date | None = None,
    effective_to: date | None = None,
    note: str | None = None,
    ctx: Any = None,
) -> dict:
    """The owner toggle: apply (or switch off) one rule for this hotel,
    optionally narrowed to confirmed dates. Upsert on (hotel, rule); audited
    as tax.override.set when ctx (HotelCtx or Principal) is given."""
    from clefs.core.errors import ClefsError

    if effective_from and effective_to and effective_to < effective_from:
        raise ClefsError(422, "effective_to is before effective_from", "invalid_dates")
    rid = _rule_id(cur, rule)
    cur.execute(
        "SELECT * FROM hotel_tax_overrides WHERE hotel_slug = %s AND fee_rule_id = %s",
        (hotel_slug, rid),
    )
    before = cur.fetchone()
    cur.execute(
        """
        INSERT INTO hotel_tax_overrides (hotel_slug, fee_rule_id, apply, effective_from, effective_to, note,
                                         acknowledged_by, acknowledged_at)
        VALUES (%(slug)s, %(rid)s, %(apply)s, %(f)s, %(t)s, %(note)s, %(uid)s, NOW())
        ON CONFLICT (hotel_slug, fee_rule_id) DO UPDATE SET
            apply = EXCLUDED.apply, effective_from = EXCLUDED.effective_from,
            effective_to = EXCLUDED.effective_to, note = EXCLUDED.note,
            acknowledged_by = EXCLUDED.acknowledged_by, acknowledged_at = NOW()
        RETURNING *
        """,
        {"slug": hotel_slug, "rid": rid, "apply": bool(apply), "f": effective_from, "t": effective_to,
         "note": note, "uid": _user_id(ctx)},
    )
    after = dict(cur.fetchone())
    _audit(cur, ctx, hotel_slug, "tax.override.set", "fee_rule", rid,
           before=dict(before) if before else None, after=after)
    return after


def get_tax_profile(cur: Any, hotel_slug: str, *, on: date) -> dict:
    """The effective profile on `on`: {property_category, convention, trn,
    td_per_bedroom, effective_from, source: 'profile'|'hotel'}."""
    ctx = load_context(cur, hotel_slug, on, on)
    row = ctx.profile_row(on)
    return {
        "property_category": ctx.category(on),
        "convention": ctx.convention(on),
        "trn": (row or {}).get("trn"),
        "td_per_bedroom": ctx.td_per_bedroom(on),
        "effective_from": (row or {}).get("effective_from"),
        "emirate": ctx.emirate,
        "currency": ctx.currency,
        "source": "profile" if row else "hotel",
    }


def set_tax_profile(
    cur: Any,
    hotel_slug: str,
    *,
    effective_from: date,
    property_category: str,
    convention: str = "A",
    trn: str | None = None,
    td_per_bedroom: bool | None = None,
    ctx: Any = None,
) -> dict:
    """Upsert the hotel's category and convention from `effective_from` on
    (a hotel can change convention mid-period, 02 spec 3.13)."""
    from clefs.core.errors import ClefsError

    conv = (convention or "A").strip().upper()
    if conv not in ("A", "B", "C"):
        raise ClefsError(422, "convention must be A, B or C", "invalid_convention")
    if property_category not in PROPERTY_CATEGORIES:
        raise ClefsError(422, f"unknown property category {property_category!r}", "invalid_property_category")
    cur.execute(
        """
        INSERT INTO hotel_tax_profiles (hotel_slug, effective_from, property_category, convention, trn,
                                        td_per_bedroom, updated_by, updated_at)
        VALUES (%(slug)s, %(f)s, %(cat)s, %(conv)s, %(trn)s, %(pb)s, %(uid)s, NOW())
        ON CONFLICT (hotel_slug, effective_from) DO UPDATE SET
            property_category = EXCLUDED.property_category, convention = EXCLUDED.convention,
            trn = EXCLUDED.trn, td_per_bedroom = EXCLUDED.td_per_bedroom,
            updated_by = EXCLUDED.updated_by, updated_at = NOW()
        RETURNING *
        """,
        {"slug": hotel_slug, "f": effective_from, "cat": property_category, "conv": conv, "trn": trn,
         "pb": td_per_bedroom, "uid": _user_id(ctx)},
    )
    row = dict(cur.fetchone())
    _audit(cur, ctx, hotel_slug, "tax.profile.set", "hotel_tax_profile", effective_from.isoformat(), after=row)
    return row


def configure_rule(
    cur: Any,
    hotel_slug: str,
    *,
    fee_code: str,
    calc_mode: str,
    rate: Any,
    effective_from: date,
    label: str,
    verified_by: str,
    base: list[str] | tuple[str, ...] = ("ROOM",),
    effective_to: date | None = None,
    property_category: str = "*",
    vat_applies: bool = True,
    cap_consecutive_nights: int | None = None,
    per_bedroom: bool = False,
    params: dict | None = None,
    source_url: str | None = None,
    ctx: Any = None,
) -> dict:
    """An OWNER_CONFIGURED rule of one hotel: the Abu Dhabi service charge,
    or any fee in Sharjah, Ajman, RAK, Fujairah or UAQ (no verified defaults
    exist there). verified_by names who checked it (the owner, a document)."""
    from psycopg2.extras import Json

    from clefs.core.errors import ClefsError
    from clefs.core.hotels import get_hotel

    if not (verified_by or "").strip():
        raise ClefsError(422, "verified_by is required for an owner-configured rule", "verified_by_required")
    em = emirate_code(get_hotel(cur, hotel_slug).emirate)
    if em is None:
        raise ClefsError(422, "the hotel has no emirate", "emirate_required")
    cur.execute(
        """
        INSERT INTO fee_rules (hotel_slug, emirate, property_category, fee_code, calc_mode, rate, base,
                               vat_applies, cap_consecutive_nights, per_bedroom, effective_from, effective_to,
                               rule_status, label, params, source_url, verified_by, verified_at, created_by)
        VALUES (%(slug)s, %(em)s, %(cat)s, %(code)s, %(mode)s, %(rate)s, %(base)s, %(vat)s, %(cap)s, %(pb)s,
                %(f)s, %(t)s, 'OWNER_CONFIGURED', %(label)s, %(params)s, %(url)s, %(vb)s, NOW(), %(uid)s)
        RETURNING *
        """,
        {"slug": hotel_slug, "em": em, "cat": property_category, "code": fee_code.upper(),
         "mode": calc_mode.upper(), "rate": D(rate), "base": list(base), "vat": vat_applies,
         "cap": cap_consecutive_nights, "pb": per_bedroom, "f": effective_from, "t": effective_to,
         "label": label, "params": Json(params or {}), "url": source_url, "vb": verified_by.strip(),
         "uid": _user_id(ctx)},
    )
    row = dict(cur.fetchone())
    _audit(cur, ctx, hotel_slug, "tax.rule.configure", "fee_rule", row["id"], after=row)
    return row
