"""flag_service_recovery (05 spec, recovery before the review): the guest
says a job never came or is still not fixed. A job marked done reopens with
a case (service.api.guest_feedback fixed=False); one still open is flagged to
the duty manager (service.api.flag_recovery). Only the guest's own jobs."""

from __future__ import annotations

from clefs.modules.concierge import tools
from tests.modules.concierge.test_tools import call, fake_service, mint  # noqa: F401 (fixture)


def _towels(client, s, call_id="c-towels"):
    out = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Two towels", "quantity": 2},
               call_id).json()
    assert out["status"] == "created"
    return out


def test_schema_and_prompt_offer_the_tool_everywhere(cur, hotel_uae):
    from clefs.modules.concierge import api

    for surface in ("web_room", "web_location", "phone", "text_fallback", "whatsapp"):
        names = {t["name"] for t in tools.tool_schemas(surface, can_order=True)}
        assert "flag_service_recovery" in names, surface
    schema = next(t for t in tools.tool_schemas("web_room") if t["name"] == "flag_service_recovery")
    assert schema["parameters"]["required"] == ["problem", "summary_en"]
    assert "room" not in schema["parameters"]["properties"], "the room is always the session's"
    spec = api.build_surface_session(hotel_uae.slug, surface="web_room", room="101", cur=cur)
    assert "flag_service_recovery" in spec.instructions and "the towels never came" in spec.instructions


def test_an_open_job_that_never_came_is_flagged(client, cur, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    _towels(client, s)
    out = call(client, s, "flag_service_recovery", {
        "problem": "never_arrived", "request_type": "towels", "summary_en": "Towels asked for an hour ago never came.",
        "original_text": "my towels never came!",
    }).json()
    assert out["status"] == "flagged" and out["reopened"] is False and out["request_ref"] == "1001"
    assert out["ui"] == {"kind": "recovery", "token": "tok-1001"}
    assert "manager" in out["followup_instructions"] and "compensation" in out["followup_instructions"]
    [case] = fake_service.recoveries
    assert case["kind"] == "never_arrived" and case["request_id"] == 1001 and case["room"] == "101"
    assert 'Guest said: "my towels never came!"' in case["summary"] and case["session_id"] == s["session_id"]
    assert fake_service.feedback == []
    cur.execute("SELECT ref FROM cc_session_events WHERE session_id = %s AND kind = 'recovery'", (s["session_id"],))
    ref = cur.fetchone()["ref"]
    assert ref["how"] == "flagged" and ref["request_id"] == 1001 and ref["case_id"] == 71


def test_a_job_marked_done_reopens(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    _towels(client, s)
    call(client, s, "notify_staff", {"request_type": "ac", "description_en": "AC not cooling"}, "c-ac")
    fake_service.states["tok-1002"] = "done"
    out = call(client, s, "flag_service_recovery", {"problem": "not_fixed", "request_type": "ac",
                                                    "summary_en": "The AC is still not cooling."}).json()
    assert out["reopened"] is True and out["ui"]["token"] == "tok-1002"
    assert fake_service.feedback == [{"hotel_slug": hotel_uae.slug, "token": "tok-1002", "fixed": False,
                                      "comment": "The AC is still not cooling."}]
    assert fake_service.recoveries == [], "guest_feedback opens the case itself"


def test_too_late_to_reopen_still_reaches_the_duty_manager(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    _towels(client, s)
    fake_service.states["tok-1001"] = "done"
    fake_service.refuse_feedback = True
    out = call(client, s, "flag_service_recovery", {"problem": "never_arrived",
                                                    "summary_en": "Says the towels never came."}).json()
    assert out["status"] == "flagged" and out["reopened"] is False
    [case] = fake_service.recoveries
    assert case["kind"] == "not_fixed" and case["request_id"] == 1001, "it was marked done: not fixed"


def test_no_matching_job_flags_without_one(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    _towels(client, s)
    out = call(client, s, "flag_service_recovery", {"problem": "not_fixed", "request_type": "plumbing",
                                                    "summary_en": "The shower is still leaking."}).json()
    assert out["status"] == "flagged" and "request_ref" not in out and out["ui"] == {"kind": "recovery", "token": None}
    [case] = fake_service.recoveries
    assert case["request_id"] is None and case["kind"] == "not_fixed", "never another job of this guest's"


def test_another_rooms_job_is_never_touched(client, hotel_uae, fake_service):
    theirs = mint(client, hotel_uae.slug, room="102")
    _towels(client, theirs)
    mine = mint(client, hotel_uae.slug, room="101")
    out = call(client, mine, "flag_service_recovery", {"problem": "never_arrived", "request_ref": "1001",
                                                       "summary_en": "Towels never came."}).json()
    assert "request_ref" not in out and fake_service.feedback == []
    assert fake_service.recoveries[0]["request_id"] is None and fake_service.recoveries[0]["room"] == "101"


def test_dry_run_files_nothing(cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api

    spec = api.build_surface_session(hotel_uae.slug, surface="eval", room="101", cur=cur)
    out = tools.execute(cur, tools.synthetic_session(spec), "flag_service_recovery",
                        {"problem": "not_fixed", "summary_en": "AC still broken"})
    assert out["status"] == "flagged" and out["dry_run"] is True
    assert fake_service.recoveries == [] and fake_service.feedback == []
