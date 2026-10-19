"""The service.api functions the 05 spec adds (section 3), pinned the way
tests/foundation/test_module_contracts.py pins the frozen ones: the
concierge (create_job, flag_recovery), the guest page (guest_feedback,
guest_messages) and the console build against these exact signatures."""

from __future__ import annotations

import inspect

from clefs.modules.service import api

DESK_API = {
    "create_job": "(cur, hotel_slug, *, room, request_type, quantity, description_en, original_text, guest_language, "
                  "priority='normal', mood=None, frustration=None, session_id=None, guest_ref=None, channel='concierge', "
                  "idempotency_key=None)",
    "dispatch_info": "(cur, hotel_slug, request_id)",
    "flag_recovery": "(cur, hotel_slug, *, kind, room, summary, request_id=None, session_id=None, guest_language=None)",
    "guest_feedback": "(cur, hotel_slug, tracking_token, *, fixed, comment=None)",
    "guest_messages": "(cur, hotel_slug, tracking_token)",
}


def _plain(fn) -> str:
    sig = inspect.signature(fn)
    params = [p.replace(annotation=inspect.Parameter.empty) for p in sig.parameters.values()]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


def test_desk_api_signatures_match_the_spec():
    for name, expected in DESK_API.items():
        assert _plain(getattr(api, name)) == expected, name


def test_the_frozen_functions_are_untouched():
    assert _plain(api.create_request) == ("(cur, hotel_slug, *, room, type, description, source, subtype=None, "
                                          "priority_hint=None, language=None, guest_ref=None, idempotency_key=None)")
    assert _plain(api.request_status_for_guest) == "(cur, hotel_slug, tracking_token)"
