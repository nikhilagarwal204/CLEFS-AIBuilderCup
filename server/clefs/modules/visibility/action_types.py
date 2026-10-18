"""
Visibility action types and verifiers (04 spec 5.7). decide_perm for every
type is `visibility.act`; none carries a money value (Value NULL), so none
counts toward verified profit.

| Type | Executor | Verifier |
|---|---|---|
| parity_fix | human task (revenue / channel manager) | same dates' property details within tolerance (24 h, daily to 7 d) |
| gbp_utm_patch | locations.patch websiteUri | the GET shows the tagged URI (1 h) |
| lodging_sync | updateLodging | getGoogleUpdated shows no override (24 h) |
| ota_listing_fix | human task | next fact-check batch clean (the AI radar's hook) |
| site_fact_fix | human task | the next JSON-LD check matches the KB |
| add_kb_faq | concierge.api.propose_kb_entry (unapproved) | kb_entry_status approved within 14 d |

A fact error is routed by the domain it cites: OTA -> ota_listing_fix,
official site -> site_fact_fix, Google -> lodging_sync, none -> add_kb_faq
(`route_fact_error`).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from decimal import Decimal

from clefs.core import actions
from clefs.core.actions import ActionType, ExecResult, VerifyResult
from clefs.modules.visibility import xmod

PERM = "visibility.act"


def _ev(label: str, value: Any, kind: str = "row") -> dict:
    return {"kind": kind, "label": label, "value": value}


# ─── parity_fix ───


def _parity_verify(ctx: Any) -> VerifyResult:
    from datetime import date

    from clefs.core import serpapi
    from clefs.core.errors import NotConfigured
    from clefs.core.hotels import get_hotel
    from clefs.core.time import local_date
    from clefs.modules.profit_guard import api as pg_api
    from clefs.modules.visibility import google_hotels, store

    p = ctx.params
    try:
        ci, co = date.fromisoformat(p["check_in"]), date.fromisoformat(p["check_out"])
    except (KeyError, TypeError, ValueError):
        return VerifyResult(status="not_verified", evidence=[_ev("params", "no stay dates to re-check")])
    hotel = get_hotel(ctx.cur, ctx.hotel_slug)
    if ci < local_date(hotel):
        return VerifyResult(status="not_verified", evidence=[_ev("stay date", "passed before a clean re-check")])
    profile = store.get_profile(ctx.cur, ctx.hotel_slug)
    token = profile.get("gh_property_token")
    if not (profile["serpapi_enabled"] and token):
        return VerifyResult(status="pending", evidence=[_ev("Google Hotels", "not connected")])
    q = f"{hotel.name} {hotel.city or ''}".strip()
    try:
        details = google_hotels.fetch_details(hotel, q, token, ci, co, max_age=timedelta(hours=1))
    except (NotConfigured, serpapi.BudgetExhausted, serpapi.SerpApiError) as exc:
        return VerifyResult(status="pending", evidence=[_ev("SerpApi", type(exc).__name__)])
    a = google_hotels.analyse_prices(details, hotel.currency)
    tolerance = xmod.call(ctx.cur, Decimal("1.0"), pg_api.parity_tolerance_pct, ctx.hotel_slug)
    ev = [_ev("Google Hotels prices", a["prices"]), _ev("gap_pct", str(google_hotels.q3(a["parity_gap_pct"])),
                                                        "calc")]
    if a["parity_gap_minor"] is not None and not google_hotels.is_breach(a["parity_gap_pct"], tolerance):
        return VerifyResult(status="verified", attribution="caused", evidence=ev, confidence=0.7)
    return VerifyResult(status="pending", evidence=ev)


# ─── ota_listing_fix (the AI radar's verifier, looked up lazily) ───


def _ota_listing_verify(ctx: Any) -> VerifyResult:
    """The AI part of this lane provides `ai_verify.verify_ota_listing_fix`
    (the fact-check prompt shows 0 errors in the next batch across the
    engines that erred, n >= 8). Until it exists the action stays pending
    and lapses to not_verified at verify_until."""
    try:
        from clefs.modules.visibility import ai_verify  # type: ignore[attr-defined]
    except ImportError:
        return VerifyResult(status="pending", evidence=[_ev("AI radar", "not built")])
    return ai_verify.verify_ota_listing_fix(ctx)


# ─── site_fact_fix ───


def _site_fact_verify(ctx: Any) -> VerifyResult:
    from clefs.modules.visibility import site_health

    since = ctx.action.get("executed_at") or ctx.action.get("decided_at")
    check = site_health.latest_check(ctx.cur, ctx.hotel_slug, since=since)
    if check is None:
        return VerifyResult(status="pending", evidence=[_ev("JSON-LD", "waiting for the next site check")])
    wanted = set(ctx.params.get("facts") or [])
    left = [m for m in (check.get("facts_vs_kb") or []) if m.get("status") == "mismatch"
            and (not wanted or m.get("fact_key") in wanted)]
    if left:
        return VerifyResult(status="not_verified", evidence=[_ev("Still different", left)])
    return VerifyResult(status="verified", attribution="caused", evidence=[_ev("JSON-LD", "matches the KB")])


# ─── add_kb_faq ───


def _kb_faq_validate(ctx: Any, params: dict) -> None:
    if not (params.get("question_en") or "").strip():
        raise ValueError("question_en is required")


def _kb_faq_execute(ctx: Any) -> ExecResult:
    """Queue the answer as an UNAPPROVED KB entry; a person approves it in the
    Concierge KB editor. Without an answer the owner must adjust the action
    first (the web told us the question, never the truth)."""
    from clefs.core.errors import ClefsError, NotConfigured
    from clefs.modules.concierge import api as concierge_api

    p = ctx.params
    if not (p.get("answer_en") or "").strip():
        return ExecResult(ok=False, error="answer_en is required: adjust the action with the correct answer")
    # Our own savepoint (not xmod.call) because the refusal's wording matters
    # here: it is shown on the failed action ("fact value must be a time...").
    ctx.cur.execute("SAVEPOINT vis_kb_faq")
    try:
        entry_id = concierge_api.propose_kb_entry(
            ctx.cur, ctx.hotel_slug, question_en=p["question_en"], answer_en=p["answer_en"],
            question_ar=p.get("question_ar"), answer_ar=p.get("answer_ar"), fact_key=p.get("fact_key"),
            source="visibility", actor=ctx.actor)
    except NotConfigured:
        ctx.cur.execute("ROLLBACK TO SAVEPOINT vis_kb_faq")
        return ExecResult(ok=False, error="Concierge KB proposals are unavailable; add the answer by hand")
    except ClefsError as exc:
        ctx.cur.execute("ROLLBACK TO SAVEPOINT vis_kb_faq")
        return ExecResult(ok=False, error=exc.detail)
    ctx.cur.execute("RELEASE SAVEPOINT vis_kb_faq")
    return ExecResult(ok=True, data={"entry_id": entry_id})


def _kb_faq_verify(ctx: Any) -> VerifyResult:
    from clefs.modules.concierge import api as concierge_api

    entry_id = (ctx.action.get("execution_result") or {}).get("entry_id")
    if not entry_id:
        return VerifyResult(status="not_verified", evidence=[_ev("KB", "no entry was queued")])
    st = xmod.call(ctx.cur, None, concierge_api.kb_entry_status, ctx.hotel_slug, int(entry_id))
    if st and st.get("status") == "approved":
        return VerifyResult(status="verified", attribution="caused", evidence=[_ev("KB entry", st)])
    if not st or st.get("status") in ("rejected", "archived"):
        return VerifyResult(status="not_verified", evidence=[_ev("KB entry", st)])
    return VerifyResult(status="pending", evidence=[_ev("KB entry", st)])


# ─── Routing ───

OTA_DOMAINS = ("booking.com", "agoda.", "expedia.", "hotels.com", "trip.com", "tripadvisor.", "trivago.")
GOOGLE_DOMAINS = ("google.", "g.page", "goo.gl")


def route_fact_error(cited_domains: list[str], *, website_host: str | None) -> str:
    """The action type for a fact error, by the domain it cites (5.7)."""
    doms = [d.lower() for d in cited_domains or [] if d]
    host = (website_host or "").lower().removeprefix("www.")
    if any(any(o in d for o in OTA_DOMAINS) for d in doms):
        return "visibility.ota_listing_fix"
    if host and any(d.removeprefix("www.") == host or d.endswith("." + host) for d in doms):
        return "visibility.site_fact_fix"
    if any(any(g in d for g in GOOGLE_DOMAINS) for d in doms):
        return "visibility.lodging_sync"
    return "visibility.add_kb_faq"


# ─── Registration ───


def _gbp_utm_execute(ctx: Any) -> Any:
    from clefs.modules.visibility import gbp

    return gbp.utm_execute(ctx)


def _gbp_utm_verify(ctx: Any) -> Any:
    from clefs.modules.visibility import gbp

    return gbp.utm_verify(ctx)


def _lodging_execute(ctx: Any) -> Any:
    from clefs.modules.visibility import lodging

    return lodging.execute(ctx)


def _lodging_verify(ctx: Any) -> Any:
    from clefs.modules.visibility import lodging

    return lodging.verify(ctx)


actions.register_action_type(ActionType(
    key="visibility.parity_fix", module="visibility", label="Fix a Google Hotels price gap",
    executor=None, verifier=_parity_verify, verify_after=timedelta(hours=24), verify_every=timedelta(days=1),
    verify_until=timedelta(days=7), default_owner_role="revenue", default_level="approve", max_level="approve",
    decide_perm=PERM,
    description="A task for the revenue or channel manager: bring the OTA's price on Google back within the parity "
                "tolerance. Your booking-engine landing price and taxes must match the price Google shows (Google "
                "hotel price accuracy policy 6064419).",
))

actions.register_action_type(ActionType(
    key="visibility.gbp_utm_patch", module="visibility", label="Tag the Google Business Profile website link",
    executor=_gbp_utm_execute, verifier=_gbp_utm_verify, verify_after=timedelta(hours=1),
    verify_until=timedelta(days=2), default_owner_role="gm", default_level="approve", max_level="auto",
    decide_perm=PERM, reversible=True,
    description="Adds utm_source=google&utm_medium=organic&utm_campaign=gbp_listing to the profile's website link, "
                "keeping its other parameters, so direct bookings from Google can be counted.",
))

actions.register_action_type(ActionType(
    key="visibility.lodging_sync", module="visibility", label="Update Google's hotel facts",
    executor=_lodging_execute, verifier=_lodging_verify, verify_after=timedelta(hours=24),
    verify_until=timedelta(days=3), default_owner_role="gm", default_level="approve", max_level="auto",
    decide_perm=PERM, reversible=True,
    description="Writes the approved check-in/out times, parking, pool, breakfast, pets and Wi-Fi facts to the Google "
                "Business Profile lodging record (those fields only).",
))

actions.register_action_type(ActionType(
    key="visibility.ota_listing_fix", module="visibility", label="Correct a fact on an OTA listing",
    executor=None, verifier=_ota_listing_verify, verify_after=timedelta(days=7), verify_every=timedelta(days=7),
    verify_until=timedelta(days=28), default_owner_role="gm", default_level="approve", max_level="approve",
    decide_perm=PERM,
    description="A task naming the OTA and the field that AI answers quote wrongly.",
))

actions.register_action_type(ActionType(
    key="visibility.site_fact_fix", module="visibility", label="Correct a fact on the hotel website",
    executor=None, verifier=_site_fact_verify, verify_after=timedelta(days=1), verify_every=timedelta(days=1),
    verify_until=timedelta(days=21), default_owner_role="gm", default_level="approve", max_level="approve",
    decide_perm=PERM,
    description="A task: make the website (and its structured data) state what the approved KB states.",
))

actions.register_action_type(ActionType(
    key="visibility.add_kb_faq", module="visibility", label="Add a missing answer to the knowledge base",
    executor=_kb_faq_execute, verifier=_kb_faq_verify, validate_params=_kb_faq_validate,
    verify_after=timedelta(days=1), verify_every=timedelta(days=1), verify_until=timedelta(days=14),
    default_owner_role="gm", default_level="approve", max_level="auto", decide_perm=PERM,
    description="Queues the answer as an unapproved Concierge KB entry; a person approves it in the KB editor.",
))
