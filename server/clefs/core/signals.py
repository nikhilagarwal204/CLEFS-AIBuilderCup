"""
Signals: candidate brief items any module emits (Canon 6, 01 spec E1, 04 spec F7).

    register_signal_kind("profit.leak_detected", label="OTA leak", default_owner_role="finance",
                         section="money_lost", cadence="daily")

    upsert_signal(cur, slug, SignalIn(
        module="profit_guard", kind="profit.leak_detected", dedupe_key="pg:L3:booking:2026-09",
        title="12 Booking no-show commissions", impact_amount=Decimal("1020.00"), currency="AED",
        confidence=0.9, evidence=[Evidence(kind="row", label="Statement line", ref={"table": "pfg_lines", "id": 7})],
        recommended_action=RecommendedAction(type="profit.dispute", label="Dispute with Booking.com"),
        due_at=deadline))
    resolve_missing(cur, slug, "profit_guard", "profit.leak_detected", seen_keys)

Detectors are idempotent: upsert_signal refreshes the row with the same
(hotel, dedupe_key) instead of duplicating it, and resolve_missing closes the
ones a detector run no longer sees. Ranking into the daily top 5 is Brief's
job; compliance items never rank there (Canon 6), which is why `section` is
mandatory (it defaults from the registered kind).

Reopening. A signal a person closed (ignored, decided, snoozed) stays closed
when the detector sees it again, unless its impact has grown by at least
reopen_if_impact_grows_pct since it was closed. A signal the machine closed
(resolved as no longer seen, expired, superseded) reopens as soon as it is
seen again: the problem came back.

Money: impact is stored as impact_minor + currency; SignalIn accepts either
impact_minor or a Decimal impact_amount, and the API returns both.

Cadence (04 spec F7, Canon 18): every kind declares the brief it belongs to
(daily = money at risk and service L1 only; weekly; monthly; there is no
default), and one signal may override it (SignalIn.cadence, e.g. "daily only
when L1"). The override is re-evaluated on every upsert: a signal upserted
without one falls back to the kind's cadence. A signal of an unregistered kind
must give both section and cadence (400 unknown_signal_kind otherwise). Brief
reads list_signals(cadence=...).

Keys: a kind is "<prefix>.<what>", where the prefix is the owning module's
name, a route segment, or a short prefix in registry.KEY_PREFIXES (04 spec
F9: "inq.", "vis.", "fin.", "pg.", ...). KEY_PREFIXES is re-exported here
because the 04 spec names this module as its home.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from clefs.core import clock
from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.registry import KEY_PREFIXES  # noqa: F401 - re-exported (04 spec F9)

Section = Literal["money_lost", "money_available", "service_risk", "reviews", "verified", "compliance"]
Cadence = Literal["daily", "weekly", "monthly"]
SECTIONS: tuple[str, ...] = ("money_lost", "money_available", "service_risk", "reviews", "verified", "compliance")
CADENCES: tuple[str, ...] = ("daily", "weekly", "monthly")
OPEN_STATUSES: tuple[str, ...] = ("open", "surfaced")
# Closed by a person: only reopened when the impact grows.
HUMAN_CLOSED = ("ignored", "decided", "snoozed")
# Closed by the machine: reopened whenever the detector sees it again.
MACHINE_CLOSED = ("resolved", "expired", "superseded", "suppressed")
SIGNAL_STATUSES = OPEN_STATUSES + HUMAN_CLOSED + MACHINE_CLOSED

SIGNAL_COLUMNS = (
    "id, hotel_slug, module, kind, dedupe_key, section, cadence, title, summary, severity, impact_minor, "
    "currency, impact_kind, confidence, evidence, recommended_action, owner_role, owner_user_id, due_at, "
    "subject_type, subject_id, status, snoozed_until, occurrences, first_seen_at, last_seen_at, surfaced_at, "
    "resolved_at, resolution, expires_at, created_at, updated_at"
)


# ─── Models ───


class Evidence(BaseModel):
    kind: Literal["metric", "row", "file", "quote", "link", "calc", "series"]
    label: str
    value: Any | None = None
    ref: dict | None = None  # {"file_id","page"} | {"table","id"} | {"metric","query"} | {"url"}


class RecommendedAction(BaseModel):
    type: str  # "<module>.<verb>", a registered action type
    label: str
    params: dict = {}
    owner_role: str | None = None
    due_in_hours: int | None = None
    expected_value: Decimal | None = None


class SignalIn(BaseModel):
    module: str
    kind: str
    dedupe_key: str = Field(min_length=1, max_length=400)
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = None
    section: Section | None = None  # defaults from the registered kind (required for an unregistered one)
    cadence: Cadence | None = None  # overrides the kind's (e.g. "daily only when L1"); required for an unregistered kind
    severity: int = Field(default=2, ge=1, le=4)
    impact_minor: int | None = None
    impact_amount: Decimal | None = None  # alternative to impact_minor, in major units
    impact_kind: str | None = None  # recoverable | revenue_upside | cost_leak | risk | service
    currency: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = []
    recommended_action: RecommendedAction | None = None
    owner_role: str | None = None
    owner_user_id: int | None = None
    due_at: datetime | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    expires_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def _ccy(cls, v: str | None) -> str | None:
        return money.ccy(v) if v else None

    @model_validator(mode="after")
    def _money(self) -> "SignalIn":
        if (self.impact_minor is not None or self.impact_amount is not None) and not self.currency:
            raise ValueError("an impact needs a currency")
        if self.impact_minor is not None and self.impact_amount is not None:
            if money.to_minor(self.impact_amount, self.currency or "AED") != self.impact_minor:
                raise ValueError("impact_minor and impact_amount disagree")
        return self

    def minor(self) -> int | None:
        if self.impact_minor is not None:
            return int(self.impact_minor)
        if self.impact_amount is not None and self.currency:
            return money.to_minor(self.impact_amount, self.currency)
        return None


# ─── Kinds ───


@dataclass(frozen=True)
class SignalKind:
    key: str
    label: str
    default_owner_role: str
    section: str
    cadence: str
    icon: str | None
    module: str
    registered_from: str


_kinds: dict[str, SignalKind] = {}
_lock = threading.Lock()


def _caller_module(depth: int = 2) -> str:
    import sys

    return sys._getframe(depth).f_globals.get("__name__", "?")


def register_signal_kind(
    key: str,
    *,
    label: str,
    default_owner_role: str,
    section: Section,
    cadence: Cadence,
    icon: str | None = None,
) -> SignalKind:
    """Declare a kind once per process (idempotent for the same caller).

    cadence has no default (04 spec F7): the daily brief is only money at
    risk and service L1 (Canon 18), so a kind that forgot to say would land
    in it silently. Every kind states the brief it belongs to."""
    if "." not in key:
        raise ValueError(f"signal kind {key!r} must look like '<module>.<what>'")
    if section not in SECTIONS:
        raise ValueError(f"section must be one of {SECTIONS}")
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    from clefs.jobs import owner_module

    origin = _caller_module()
    kind = SignalKind(
        key=key,
        label=label,
        default_owner_role=default_owner_role,
        section=section,
        cadence=cadence,
        icon=icon,
        module=owner_module(origin),
        registered_from=origin,
    )
    with _lock:
        existing = _kinds.get(key)
        if existing is not None and existing.registered_from != origin:
            raise ValueError(f"signal kind {key!r} already registered by {existing.registered_from}")
        _kinds[key] = kind
    return kind


def signal_kinds() -> list[SignalKind]:
    with _lock:
        return sorted(_kinds.values(), key=lambda k: k.key)


def get_kind(key: str) -> SignalKind | None:
    with _lock:
        return _kinds.get(key)


# ─── Serialization ───


def to_api(row: dict) -> dict:
    """A signals row as the API returns it: money as a Decimal string too."""
    out = dict(row)
    ccy = (out.get("currency") or "").strip() or None
    out["currency"] = ccy
    out["impact_amount"] = money.money_str(out.get("impact_minor"), ccy)
    if out.get("confidence") is not None:
        out["confidence"] = float(out["confidence"])
    out["rankable"] = out.get("section") != "compliance"
    return out


# ─── Writes ───


def _json_list(items: list[BaseModel]) -> Json:
    return Json([i.model_dump(mode="json") for i in items])


def upsert_signal(cur: Any, hotel_slug: str, s: SignalIn, *, reopen_if_impact_grows_pct: float = 25) -> dict:
    """Insert or refresh the signal with (hotel_slug, s.dedupe_key).

    Returns the row (API shape) plus `created` and `reopened` flags."""
    kind = get_kind(s.kind)
    section = s.section or (kind.section if kind else None)
    if section is None:
        raise ClefsError(400, f"signal kind {s.kind!r} is not registered and no section was given", "unknown_signal_kind")
    cadence = s.cadence or (kind.cadence if kind else None)
    if cadence is None:
        # Same reason as register_signal_kind: no silent daily default.
        raise ClefsError(400, f"signal kind {s.kind!r} is not registered and no cadence was given", "unknown_signal_kind")
    owner_role = s.owner_role or (kind.default_owner_role if kind else None)
    now = clock.now()
    impact = s.minor()
    values = {
        "hotel_slug": hotel_slug,
        "module": s.module,
        "kind": s.kind,
        "dedupe_key": s.dedupe_key,
        "section": section,
        "cadence": cadence,
        "title": s.title,
        "summary": s.summary,
        "severity": s.severity,
        "impact_minor": impact,
        "currency": s.currency,
        "impact_kind": s.impact_kind,
        "confidence": round(float(s.confidence), 2),
        "evidence": _json_list(s.evidence),
        "recommended_action": Json(s.recommended_action.model_dump(mode="json")) if s.recommended_action else None,
        "owner_role": owner_role,
        "owner_user_id": s.owner_user_id,
        "due_at": s.due_at,
        "subject_type": s.subject_type,
        "subject_id": s.subject_id,
        "expires_at": s.expires_at,
        "now": now,
    }
    cur.execute(
        "SELECT id, status, impact_minor, closed_impact_minor, snoozed_until FROM signals "
        "WHERE hotel_slug = %s AND dedupe_key = %s FOR UPDATE",
        (hotel_slug, s.dedupe_key),
    )
    existing = cur.fetchone()
    if existing is None:
        cur.execute(
            f"""
            INSERT INTO signals (hotel_slug, module, kind, dedupe_key, section, cadence, title, summary, severity,
                                 impact_minor, currency, impact_kind, confidence, evidence, recommended_action,
                                 owner_role, owner_user_id, due_at, subject_type, subject_id, expires_at,
                                 first_seen_at, last_seen_at, created_at, updated_at)
            VALUES (%(hotel_slug)s, %(module)s, %(kind)s, %(dedupe_key)s, %(section)s, %(cadence)s, %(title)s,
                    %(summary)s, %(severity)s, %(impact_minor)s, %(currency)s, %(impact_kind)s, %(confidence)s,
                    %(evidence)s, %(recommended_action)s, %(owner_role)s, %(owner_user_id)s, %(due_at)s,
                    %(subject_type)s, %(subject_id)s, %(expires_at)s, %(now)s, %(now)s, %(now)s, %(now)s)
            RETURNING {SIGNAL_COLUMNS}
            """,
            values,
        )
        out = to_api(dict(cur.fetchone()))
        out.update(created=True, reopened=False)
        return out

    status = existing["status"]
    reopen = False
    if status in MACHINE_CLOSED:
        reopen = True
    elif status in HUMAN_CLOSED:
        base = existing["closed_impact_minor"]
        if base is None:
            base = existing["impact_minor"]
        # No money attached when it was closed: a person decided anyway, so
        # only the snooze timer (below) brings it back.
        if impact is not None and base is not None and base > 0:
            reopen = impact >= base * (1 + reopen_if_impact_grows_pct / 100.0)
        if status == "snoozed" and existing["snoozed_until"] is not None and existing["snoozed_until"] <= now:
            reopen = True
    values["reopen"] = reopen
    values["id"] = existing["id"]
    cur.execute(
        f"""
        UPDATE signals SET
            module = %(module)s, kind = %(kind)s, section = %(section)s, cadence = %(cadence)s, title = %(title)s,
            summary = %(summary)s, severity = %(severity)s, impact_minor = %(impact_minor)s, currency = %(currency)s,
            impact_kind = %(impact_kind)s, confidence = %(confidence)s, evidence = %(evidence)s,
            recommended_action = %(recommended_action)s, owner_role = %(owner_role)s,
            owner_user_id = COALESCE(%(owner_user_id)s, owner_user_id), due_at = %(due_at)s,
            subject_type = %(subject_type)s, subject_id = %(subject_id)s, expires_at = %(expires_at)s,
            occurrences = occurrences + 1, last_seen_at = %(now)s, updated_at = %(now)s,
            status = CASE WHEN %(reopen)s THEN 'open' ELSE status END,
            resolved_at = CASE WHEN %(reopen)s THEN NULL ELSE resolved_at END,
            resolution = CASE WHEN %(reopen)s THEN NULL ELSE resolution END,
            snoozed_until = CASE WHEN %(reopen)s THEN NULL ELSE snoozed_until END,
            closed_impact_minor = CASE WHEN %(reopen)s THEN NULL ELSE closed_impact_minor END,
            surfaced_at = CASE WHEN %(reopen)s THEN NULL ELSE surfaced_at END
        WHERE id = %(id)s
        RETURNING {SIGNAL_COLUMNS}
        """,
        values,
    )
    out = to_api(dict(cur.fetchone()))
    out.update(created=False, reopened=reopen)
    return out


def resolve_signal(cur: Any, hotel_slug: str, dedupe_key: str, *, resolution: str) -> bool:
    """Close an open (or snoozed/decided) signal. False if none was open."""
    now = clock.now()
    cur.execute(
        """
        UPDATE signals SET status = 'resolved', resolution = %s, resolved_at = %s, updated_at = %s
        WHERE hotel_slug = %s AND dedupe_key = %s AND status IN ('open', 'surfaced', 'snoozed', 'decided')
        """,
        (resolution, now, now, hotel_slug, dedupe_key),
    )
    return cur.rowcount > 0


def resolve_missing(cur: Any, hotel_slug: str, module: str, kind: str, seen_keys: set[str] | list[str]) -> int:
    """After a detector run: resolve this (module, kind)'s open signals whose
    dedupe_key the run did not produce. Returns how many were closed."""
    now = clock.now()
    cur.execute(
        """
        UPDATE signals SET status = 'resolved', resolution = 'not_seen', resolved_at = %s, updated_at = %s
        WHERE hotel_slug = %s AND module = %s AND kind = %s AND status IN ('open', 'surfaced', 'snoozed')
          AND NOT (dedupe_key = ANY(%s))
        """,
        (now, now, hotel_slug, module, kind, list(seen_keys)),
    )
    return cur.rowcount


def mark_surfaced(cur: Any, ids: list[int]) -> None:
    """Brief calls this for the items it put in front of a person."""
    if not ids:
        return
    now = clock.now()
    cur.execute(
        "UPDATE signals SET status = 'surfaced', surfaced_at = %s, updated_at = %s WHERE id = ANY(%s) AND status = 'open'",
        (now, now, list(ids)),
    )


def set_status(
    cur: Any,
    signal_id: int,
    status: str,
    *,
    resolution: str | None = None,
    snoozed_until: datetime | None = None,
) -> None:
    """Used by the actions ledger: decided / ignored / snoozed / resolved.
    Remembers the impact at closing so growth can reopen it later."""
    if status not in SIGNAL_STATUSES:
        raise ValueError(f"bad signal status {status!r}")
    now = clock.now()
    cur.execute(
        """
        UPDATE signals SET status = %s, updated_at = %s,
            resolution = COALESCE(%s, resolution),
            resolved_at = CASE WHEN %s IN ('resolved', 'expired', 'superseded') THEN %s ELSE resolved_at END,
            snoozed_until = %s,
            closed_impact_minor = CASE WHEN %s IN ('ignored', 'decided', 'snoozed') THEN impact_minor
                                       ELSE closed_impact_minor END
        WHERE id = %s
        """,
        (status, now, resolution, status, now, snoozed_until, status, signal_id),
    )


def expire_due(cur: Any, *, hotel_slug: str | None = None) -> int:
    """Expire open signals past expires_at; wake snoozed ones whose time came."""
    now = clock.now()
    cur.execute(
        """
        UPDATE signals SET status = 'expired', resolved_at = %s, updated_at = %s, resolution = 'expired'
        WHERE status IN ('open', 'surfaced') AND expires_at IS NOT NULL AND expires_at <= %s
          AND (%s::TEXT IS NULL OR hotel_slug = %s)
        """,
        (now, now, now, hotel_slug, hotel_slug),
    )
    n = cur.rowcount
    cur.execute(
        """
        UPDATE signals SET status = 'open', snoozed_until = NULL, closed_impact_minor = NULL, updated_at = %s
        WHERE status = 'snoozed' AND snoozed_until IS NOT NULL AND snoozed_until <= %s
          AND (%s::TEXT IS NULL OR hotel_slug = %s)
        """,
        (now, now, hotel_slug, hotel_slug),
    )
    return n + cur.rowcount


# ─── Reads ───


def _cadences(cadence: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    """None (every cadence), one cadence, a list, or "daily,weekly" as the
    route passes it through. 400 invalid_cadence for anything else."""
    if cadence is None:
        return None
    items = cadence.split(",") if isinstance(cadence, str) else list(cadence)
    out = [c.strip() for c in items if c and c.strip()]
    bad = [c for c in out if c not in CADENCES]
    if bad:
        raise ClefsError(400, f"unknown cadence {bad[0]!r}", "invalid_cadence")
    return out or None


def list_signals(
    cur: Any,
    hotel_slug: str,
    *,
    status: tuple[str, ...] | list[str] = OPEN_STATUSES,
    modules: list[str] | None = None,
    sections: list[str] | None = None,
    cadence: str | list[str] | tuple[str, ...] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Signals for a hotel, most urgent first (earliest due, then largest impact).
    Each row carries `action_id`: the live (non-terminal) action for it.
    `cadence` filters on the stored cadence (the per-signal override, else the
    kind's): one value, several, or a comma-separated string."""
    limit = max(1, min(int(limit), 500))
    cadences = _cadences(cadence)
    # The status list is actions.TERMINAL spelled out (actions imports this
    # module, not the other way round). A failed or expired action is over:
    # the reopened signal must offer a new action, not link the dead one.
    cur.execute(
        f"""
        SELECT {", ".join("s." + c.strip() for c in SIGNAL_COLUMNS.split(","))},
               (SELECT a.id FROM actions a
                 WHERE a.signal_id = s.id
                   AND a.status NOT IN ('verified', 'not_verified', 'rejected', 'cancelled', 'unresolved', 'observed',
                                        'failed', 'expired')
                 ORDER BY a.id DESC LIMIT 1) AS action_id
        FROM signals s
        WHERE s.hotel_slug = %(h)s AND s.status = ANY(%(status)s)
          AND (%(modules)s::TEXT[] IS NULL OR s.module = ANY(%(modules)s))
          AND (%(sections)s::TEXT[] IS NULL OR s.section = ANY(%(sections)s))
          AND (%(cadence)s::TEXT[] IS NULL OR s.cadence = ANY(%(cadence)s))
        ORDER BY s.due_at ASC NULLS LAST, s.impact_minor DESC NULLS LAST, s.severity DESC, s.id DESC
        LIMIT %(limit)s
        """,
        {
            "h": hotel_slug,
            "status": list(status),
            "modules": list(modules) if modules else None,
            "sections": list(sections) if sections else None,
            "cadence": cadences,
            "limit": limit,
        },
    )
    return [to_api(dict(r)) for r in cur.fetchall()]


def get_signal(cur: Any, hotel_slug: str, signal_id: int) -> dict | None:
    cur.execute(f"SELECT {SIGNAL_COLUMNS} FROM signals WHERE hotel_slug = %s AND id = %s", (hotel_slug, signal_id))
    row = cur.fetchone()
    return to_api(dict(row)) if row else None


# ─── Core kinds ───

register_signal_kind(
    "core.autonomy_promotion",
    label="Autonomy promotion suggested",
    default_owner_role="owner",
    section="compliance",
    cadence="weekly",
)
register_signal_kind(
    "core.autonomy_demoted",
    label="Autonomy lowered after an incident",
    default_owner_role="owner",
    section="compliance",
    cadence="weekly",
)
