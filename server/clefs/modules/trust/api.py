"""
Trust public API (frozen signatures; other modules import only this file).

Trust builds audit-chain verification (over audit_log / audit_row_hash from
the foundation), the audit viewer, DSR, retention and the readiness gate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from clefs.core import pii


def readiness(cur: Any, hotel_slug: str) -> dict:
    """Go-live gate for concierge.live (03 spec 5.4): {ready, overridden,
    checks: [{key, label, ok, detail}], run_id, prompt_version,
    kb_fingerprint, evaluated_at, override}.

    Checks: kb_sections (the 8 required KB sections approved), recent_run
    (a completed run on the current prompt and KB, at most 7 days old),
    standard (all 22 standard safety and accuracy tests passed in it),
    safety (100%), routing (95%+), overall (90%+), shadow (7 days in shadow
    mode). `ready` is also true while an owner override is active (then
    overridden=True and the red checks stay red). Never raises for a missing
    run or KB: it is simply not ready. The payload also carries `tests`
    ({passed, total, expected, run_id, judge_model, finished_at}) and
    `shadow` ({state, day, days, started_at, until, escalations})."""
    from clefs.modules.trust.evals import readiness as gate

    return gate.evaluate(cur, hotel_slug)


def gate_summary(cur: Any, hotel_slug: str) -> dict:
    """The go-live gate in brief for a public page (the /try reviewer page):
    {tests: {state: passed|failed|running|none, passed, total, expected,
    run_id, judge_model, finished_at}, shadow: {state, day, days, ...}}.
    Cheap: two queries, no prompt build."""
    from clefs.modules.trust.evals import readiness as gate

    return gate.summary(cur, hotel_slug)


def queue_eval_run(cur: Any, hotel_slug: str, *, suites: Iterable[str] = ("standard",), note: str | None = None) -> dict | None:
    """Queue an eval run of `suites` (default: the 22 standard tests); the
    task trust.eval_run runs it on the next worker tick. Returns the queued
    run, or None when a run is already queued or running for the hotel."""
    from clefs.core.errors import ClefsError
    from clefs.modules.trust.evals import runs

    try:
        return runs.create_run(cur, hotel_slug, trigger="manual", suites=list(suites), note=note)
    except ClefsError as exc:
        if exc.code == "eval_run_in_progress":
            return None
        raise


# ─── PII masking (03 spec 5.2, Wave 1 preflight) ───
# The matrix itself is foundation code (clefs.core.pii), because every lane
# serializing guest data needs it in the same wave and foundation code may
# not import a module. This is the module-API door to it: Trust extends the
# matrix with new field classes (pii.register_field_class), and may wrap this
# body (per-hotel policy, audit of unmasked reads) without any caller moving.


def mask(
    obj: Any,
    role: str | None,
    *,
    field_classes: Mapping[str, str],
    departments: Iterable[str] = (),
    subject_department: str | None = None,
    department_field: str | None = "department",
) -> Any:
    """A masked copy of `obj` for a viewer with `role` (see clefs.core.pii.mask
    for the arguments). Works now: it is the foundation matrix."""
    return pii.mask(
        obj,
        role,
        field_classes=field_classes,
        departments=departments,
        subject_department=subject_department,
        department_field=department_field,
    )


# ─── Privacy restrictions (03 spec 5.3, DSR restrict / object / erase) ───
# Added in Wave 1 by the Trust lane (new function, no frozen signature moved).
# A restrict or object request withdraws the matching consents as well, so a
# module that already checks core.consent.has_consent is covered; this is
# the explicit check for code paths that do not ask for consent (for
# example aggregate analytics over stored rows).


def is_restricted(cur: Any, hotel_slug: str, subject_ref: str, *, scope: str = "marketing") -> bool:
    """True when a data subject (subject_ref = privacy.contact_ref hex, as in
    consents) restricted or objected to processing for `scope`
    (marketing, ai_analytics, profile_memory), or asked for erasure.

    A per-channel scope (marketing_whatsapp, marketing_email, ...) is also
    covered by a 'marketing' flag: a sender may ask with the purpose it is
    about to use, and a flag written before that channel existed still
    applies (04 spec 3.4 split marketing per channel after 03 spec 5.3)."""
    wanted = [scope, "all"]
    if scope.startswith("marketing_"):
        wanted.append("marketing")
    cur.execute(
        "SELECT 1 FROM trs_subject_flags WHERE hotel_slug = %s AND subject_ref = %s AND restricted "
        "AND lifted_at IS NULL AND scopes && %s::text[]",
        (hotel_slug, subject_ref, wanted),
    )
    return cur.fetchone() is not None
