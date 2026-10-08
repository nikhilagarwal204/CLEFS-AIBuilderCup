"""
Guest journeys J1-J8 (04 spec section 3): a fixed library, no campaign
builder. It knows each booking's source and each contact's consent, and
every send goes through the actions ledger, so the autonomy dial governs it.

Files:
- steps.py      step constants, action type keys, labels, collision order
- templates.py  the EN/AR template library, lint, seeding, approval
- planner.py    stay-event consumer (guests.journeys) and plan timing
- channels.py   contact eligibility and channel resolution (3.4)
- guards.py     windows, caps, complaint pause, billing readiness (3.5)
- dispatch.py   guests.journey_dispatch: claim, guard, create_action
- executor.py   action types, send_step, verifiers (3.7)
- status.py     on_outbound_status hook, Meta 131049, message cost
- replies.py    the priority-10 WhatsApp handler (3.8)
- gates.py      J6 gates, review links, J7 feedback (3.9)
- public.py     /public/journeys/{token} routes (the /g pages' data)
- router.py     console routes /hotels/{slug}/guests/journeys/...
- maintenance.py purge, opt-out monitor, template, billing, expiry jobs
- metrics.py    metric and signal registrations
- manifest.py   what module.py merges in (routers, startup)
"""

# Signal kinds, metrics and PII entries register on first import of any
# journeys file, so a job or handler that raises a signal never meets an
# unregistered kind (the app and the worker also import manifest.py).
from clefs.modules.guests.journeys import metrics as _metrics  # noqa: E402,F401
