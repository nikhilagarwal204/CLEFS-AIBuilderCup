# Guest Concierge (`concierge`)

Owned by lane **B Concierge + Voice** from Wave 1. Only that lane edits this directory (plus `tests/modules/concierge/`).

- Migrations: `migrations/NNNN_concierge_<desc>.sql|.py`, numbers 0200-0299 (`python -m clefs.migrate new --module concierge "desc"`).
  - `0200_concierge_kb.sql`: `cc_kb_sections`, `cc_kb_imports`, `cc_kb_entries`, `cc_kb_revisions`, `cc_kb_unknowns`, `cc_kb_unknown_events`.
  - `0201_concierge_sessions.sql`: `cc_sessions` (the 03 spec `concierge_sessions`), `cc_session_events`, `cc_tool_calls`, `cc_order_quotes`, `conversation_logs` additions.
  - `0202_concierge_consent_texts.py`: platform consent wording (EN/AR, version `concierge-1`, written for a UAE deployment) for `ai_processing`, `transfer_abroad`, `recording`, `ai_analytics`. Kept as the evidence earlier grants point at.
  - `0208_concierge_consent_texts_v2.py`: version `concierge-2` (`constants.CONSENT_TEXT_VERSION`), the current wording, in every guest language (en, ar, ru, hi, ur, zh, de, fr, ja, ko, th): Google Cloud's Gemini AI models may process messages outside the guest's country, including in the United States; data is stored in Google Cloud's India region. Same purposes and required flags as concierge-1.
  - `0203_concierge_turns.sql`: `cc_sessions.chat_history` (a text session's working memory, cleared when the session ends), `sender_ref` (WhatsApp sender hmac, hex), `last_safety_level`; `conversation_logs.consent_ref` and `consents` (the consent a stored turn was logged under).
- New tables start with `cc_`. Legacy tables it owns (names grandfathered, no prefix): `conversation_logs`, `hotel_locations`.
- Console routes live under `/hotels/{slug}/concierge/` (`router.py` includes `router_console.py`); guest routes under `/public/` (`router_public.py`, mounted through `legacy.router` because `module.py` lists only `router` and `legacy_router`).
- Permissions are declared in `module.py` and start with `concierge.` (`concierge.view`: owner, gm, front_desk; `concierge.manage`: owner, gm).
- `api.py` is this module's public surface. Other modules may import ONLY `clefs.modules.concierge.api`.
- `legacy.py` holds routes moved from the old `server/main.py`; their paths and JSON shapes are frozen by `tests/foundation/test_legacy_contract.py`.

## Files

| File | What |
|---|---|
| `constants.py` | Surfaces, fact vocabulary, standard KB sections, emergency numbers, consent purposes, mint limits, `ConciergeSettings` |
| `kb.py` | Structured bilingual KB: sections, entries (pending, approved, rejected, archived), revisions, `kb_mode`, legacy-text import (task `concierge.kb_import`), unknowns and the `concierge.kb_gap` signal |
| `prompt.py` | The server-built prompt: 8 ordered blocks, `prompt_version`, greeting in hotel time, `build_spec()` |
| `sessions.py` | Target resolution (`?k=` / stay token / limited mode), consent, rate limits, Gemini Live ephemeral token mint (`google-genai` `auth_tokens.create`, v1alpha), `cc_sessions` rows, bearer auth, job `concierge.session_gc` |
| `context_api.py` | The stay context body |
| `tools.py` | Tool schemas (`tool_schemas`) and the server-side executors: `notify_staff` v2, `escalate_to_human`, `get_request_status`, `get_menu`, `place_order`, `search_hotel_info`; dedupe on `sid:call_id`; room bound from the session |
| `turns.py` | Turn log with the safety double check, the tools/turns/chat routes, jobs `concierge.unknowns_cluster` / `concierge.chat_history_gc` / `concierge.upsell_performance`, metrics |
| `text_chat.py` | Responses API text chat with the same prompt and tools (HTTP fallback, WhatsApp, `api.respond_text` for evals) |
| `whatsapp.py` | WhatsApp chat handler (GUEST_PRIORITY 30), consent buttons, task `concierge.whatsapp_reply` |
| `router_public.py`, `router_console.py` | HTTP routes (below); `router_public` includes `turns.router` |
| `live.py` | The `concierge.live` switch and its readiness gate (below) |
| `transcripts.py` | Console transcripts: conversations grouped from `conversation_logs`, masked by role through `clefs.core.pii` (below) |

## Settings (`hotel_module_settings['concierge']`, model `constants.ConciergeSettings`)

`kb_mode` (`legacy_text` or `structured`; unset = legacy_text while the hotel has legacy text and no approved entry), `legacy_qr_personalization` (legacy stay route only), `daily_session_cap` (default 500 mints per hotel-local day), `voice` (Gemini Live prebuilt voice: Kore, Puck, Charon, Aoede, Fenrir, Leda, Orus, Zephyr; default `Kore`; legacy names are mapped), `chips` (`{lang: [{key, label}]}`). Unknown keys are kept. concierge.live is deliberately NOT here (see below).

## Go-live: concierge.live and the readiness gate (`live.py`, 03 spec 5.4)

No guest conversation starts while the concierge is not live: the web mint answers 409 `concierge_not_live` (the page shows "please call the front desk"), the phone mint (`api.open_session(transport='sip')`, not a dry-run spec) raises the same 409 so the gate holds even for a caller that skips `is_live`, the phone webhook rejects the call with 486 (reason `concierge_not_live`, the PBX fails over to the operator; also when the phone mint refuses after the check), and WhatsApp leaves the message to Service's staff fallback. Switching ON (`PUT /live {live:true}`) needs `trust.api.readiness()` ready (safety 100%, routing 95%+, required KB sections approved, a recent run on the current prompt and KB) or Trust's audited owner override: else 409 `readiness_not_met` naming the failing checks. An unavailable Trust gate counts as red. Switching OFF is always allowed. The switch lives in `cc_live` (migration 0204), not in these settings, because the generic `PUT /module-settings/concierge` writes that JSON with no gate; it is audited as `concierge.live_switched` with the gate's verdict. The gate checks the transition, not every mint: a later red eval does not cut guests off mid-stay.

Grandfathering: migration 0204 gave every hotel that predates the rebuild (`legacy_open_access = TRUE`, set by 0003 for all of them) a live `cc_live` row (source `grandfathered`), since it was already serving guests. It is captured once, not read from `legacy_open_access` at runtime, so an owner locking the dashboard down later does not take the guest concierge offline. An explicit OFF then ON passes the gate. Every hotel created since has no row (not live) and must pass the gate once. `api.is_live(cur, slug)` is the door for other modules.

Demo hotels (QA GS-1): the demo seed wipes and rebuilds `hotels.status = 'demo'` hotels, `cc_live` row included, and a demo has no eval run to pass the gate on, so its guest page would answer every message with "call the front desk". The seed part `clefs/seed/demo_uae/concierge_live.py` calls `api.go_live_demo(cur, slug, *, actor)`, which writes a live row with source `demo` (migration 0206) without the gate, audited as `concierge.live_switched` with `meta.demo = true`; it raises 409 `not_demo_hotel` for any other hotel. `GET /public/stay/.../context` reports `features.assistant` (and `features.voice`) from `is_live`, so the guest page says "call the front desk" on START instead of after the consent sheet.

## The prompt (03 spec 2.1)

Blocks in order: persona, disclosure (mandatory on phone), language (the guest's chosen language, any of `constants.GUEST_LANGUAGES`; Gulf-leaning MSA, Arabizi, polite Japanese, Korean and Thai registers, Western digits), approved knowledge (`[KB:id]` tags; titles and top questions plus `search_hotel_info` above about 6k tokens), menu (ordering surfaces only, from `upsell.api.get_menu`), guest context (room always; first name and preference tags only when verified; loyalty never), policy (no unlisted prices or promises, human handoff, the hotel country's emergency numbers (UAE, Japan, India; else "local emergency services"), sourced local places only, never other guests), tool rules (only for the tools the session has). No em dash, no hardcoded menu, currency or timezone.

`prompt_version` = sha256 (16 hex) of the blocks with the room, first name, preferences and local time replaced by placeholders, plus the surface: it changes when the hotel's prompt changes (KB approval, policy, tools), not per guest.

Tool schemas come from `tools.tool_schemas(surface, search=, can_order=)`: `notify_staff`, `escalate_to_human` and `get_request_status` everywhere; `get_menu` and `place_order` on ordering surfaces (not WhatsApp); `search_hotel_info` only when the approved KB is large. A `room` argument is offered only on surfaces without a room of their own (venue QR, WhatsApp, text).

## api.py

| Function | Behaviour |
|---|---|
| `build_session(hotel_slug, *, channel, room=None, location=None, language=None) -> SessionSpec` (frozen) | web maps to `web_room` (`web_location` with only a location), sip to `phone`, text to `text_fallback`, whatsapp to `whatsapp`. Never personalised. |
| `build_surface_session(hotel_slug, *, surface, room=None, location=None, language=None, verified=False, guest=None, dry_run=False, cur=None) -> SessionSpec` | The 03 spec builder. `guest` is `guests.api.guest_context_for_room(...)`, used only when `verified`. `surface="eval"` always sets `dry_run=True` and carries the text model (`AI_MODEL_CONCIERGE_TEXT`), not the Live audio model (`REALTIME_MODEL` setting): Trust's runner drives it through `respond_text` on the text path, which does not serve Live audio model ids, so runs record the text model and runner `concierge_text` (no report claims Live voice coverage). |
| `open_session(cur, spec, *, key_status="extension", call_ref=None, transport="sip", consents=None, with_token=False) -> {session_id, expires_at, prompt_version, session_token}` | A `cc_sessions` row for a phone call or an eval run. `transport='sip'` (the phone mint) needs the concierge live: 409 `concierge_not_live` otherwise (evals and the text door are exempt). |
| `run_tool(cur, hotel_slug, *, session_ctx, name, args) -> dict` (frozen, Wave 1 gaps) | The one tool executor door (03 spec 0.3) for the phone (voice MCP server and sideband worker) and evals: wraps `tools.execute_for_session`, so a phone tool files, dedupes and refuses exactly like the web tool route. `session_ctx`: `{session_id, call_id?}` (the room, its verification and the surface come from the `cc_sessions` row; a `room` argument counts only on a room-less session and is then unverified; the same `call_id` returns the stored first answer with `deduped: true`) or `{spec}` for a dry-run eval spec (synthetic session, nothing filed; a non-dry-run spec with no session is refused). `args` is a dict or the model's raw JSON string (bad JSON runs with no arguments). Returns the speakable result `{status, followup_instructions, ui?, deduped?, ...}`; never raises for an unknown, ended or expired session, a session of another hotel (all `session_ended`, indistinguishable), an unknown tool (`unknown_tool`) or another module's refusal (`error` / `rate_limited`). |
| `kb_facts(cur, hotel_slug, *, language="en", keyed_only=False)` (frozen) | Approved entries in force today as `{source: "kb_entry", text, fact_key, value, entry_id, approved_at}`; legacy paragraphs first in `legacy_text` mode (`source: "knowledge_base"`). `keyed_only`: approved structured facts only. |
| `propose_kb_entry(cur, hotel_slug, *, question_en, answer_en, question_ar=None, answer_ar=None, fact_key=None, source, actor) -> int` (frozen) | Creates a **pending** entry; a `fact_key` proposal's value is parsed from `answer_en` (400 `invalid_fact_value`). |
| `kb_entry_status(cur, hotel_slug, entry_id)` (frozen) | `{entry_id, status, approved_at}` or None (other hotel's or unknown). |
| `respond_text(spec, messages)` (frozen) | `text_chat.respond_text`: one reply to `[{role, content}]` (user/assistant text only; other roles are dropped). Tool calls run server-side. A dry-run spec (`surface="eval"`) uses a synthetic session and files nothing; any other spec gets a `cc_sessions` row (transport `none`) for its tool calls. Logs no turns (the caller keeps its transcript). `AIDisabled` / `AIError` propagate (an eval runner reports "runner unavailable"); the guest routes turn them into a hand-off sentence instead. |

`SessionSpec` additions (all defaulted): `prompt_version`, `surface`, `verified`, `tool_choice`, `greeting_text`, `kb_mode`, `dry_run`, `kb_entry_ids`.

## HTTP API

Errors are `{detail, code}`. The browser calls these through `/api/clefs`.

### Public (guests, no login)

**`GET /public/stay/{slug}/{target}/context?k=&lang=&kind=room|location&st=`** (also header `X-Stay-Token`). 60 per 10 minutes per IP hash and target, 300 per 10 minutes per IP hash overall, recorded in their own committed transaction before the lookup (a 404 cannot roll them back). Enumeration guard: 20 misses (`room_not_found` / `location_not_found`) per 10 minutes per IP hash, shared with the mint route, then 429 for every target. `target` is a room number (`101`, `room101`) or a venue slug; `kind` forces one. 404 `hotel_not_found`, `room_not_found` (not in the register, or inactive), `location_not_found`; 429 `rate_limited`.

```
{hotel:{slug,name,assistant_name,city,tz,currency,locale,languages[]},
 surface: web_room|web_location, room:{number,floor}|null, location:{slug,name,type}|null,
 verified: bool, key_status: valid|stay_token|invalid|absent,
 guest:{first_name}|null        (only when verified; never another room, never a family name)
 language, greeting_text, chips:[{key,label}], features:{menu,tracker,whatsapp,voice},
 emergency_numbers:[{number,kind}], consent_texts:{purpose:{version,language,text,required,required_for_voice}},
 consent_version: "concierge-2", stay_session_key: string|null}
```

`stay_session_key` (12 h, bound to the room and its QR key version) verifies later visits and mints without `?k=`.

**`POST /public/concierge/sessions`**

```
{hotel | hotelSlug, target:{room}|{location}, k?, stay_token? | stayToken?, lang?, mode: voice|text|chip|prewarm (voice),
 transport: live|http (live), guest_ref? | guestRef?, consents?: {ai_processing, transfer_abroad, recording, ai_analytics: bool}}
-> {session_id, session_token (bearer, 2 h), expires_at, client_secret:{value,expires_at}|null, greeting_text, prompt_version,
    surface, verified, key_status, language, model, transport, consents:{purpose: bool}, guest_ref, stay_token? (verified room)}
```

- The body may not carry `session`, `instructions`, `tools` or `tool_choice`: 409 `client_outdated` (an old cached page; "please refresh").
- Consent: `consents` are recorded (source `concierge_consent_sheet`, linked to the wording shown) against `guest_ref` (generated when absent; the page keeps it). `ai_processing` + `transfer_abroad` are required, plus `recording` for `mode=voice`: else 409 `consent_required`. `mode=prewarm` may not carry `consents` (400 `prewarm_with_consent`), so a prewarm only runs on consent already stored.
- Limits: 30/h per IP hash (checked before the target lookup), 10/10 min per room or venue, `daily_session_cap` per hotel-local day: 429 `session_rate_limited`; the shared miss guard above (429 `rate_limited`).
- 409 `concierge_not_live` while the hotel's concierge is not switched live (see Go-live).
- `transport=live` mints a single-use Gemini Live ephemeral token (30 min, session must open within 1 min) locked via `live_connect_constraints` to the server's instructions, tools (function declarations), model and voice; `client_secret` is `{value, expires_at, model}` and the browser connects with `@google/genai` `ai.live.connect`; `transport=http` (the text fallback) mints nothing and the surface is `text_fallback`. 409 `ai_disabled` when AI is off; 502 `live_mint_failed`.
- Unverified (no key, rotated key): the session works in limited mode (`verified=false`, no personal context).

**`POST /public/concierge/sessions/{session_id}/end`** with `Authorization: Bearer <session_token>`, body `{withdraw_consent?: bool, reason?}` -> `{ok, session_id, withdrawn: [purposes]}`. `withdraw_consent=true` is the Privacy button: every granted consent is withdrawn and transcripts stop. 401 `session_invalid` / `session_expired`.

**`POST /public/concierge/consent/withdraw`** body `{hotel, guest_ref | guestRef}` -> `{ok, withdrawn: [purposes]}`. The Privacy button when the page has no live session (a reload, an expired token): every consent the device's `guest_ref` granted is withdrawn and its open sessions stop storing transcripts. An unknown ref answers `withdrawn: []` (reveals nothing). 30/h per IP hash; 404 `hotel_not_found`.

**Session routes below** all take `Authorization: Bearer <session_token>` (401 `session_invalid` / `session_expired`, also for an ended session). The room is always the session's; nothing in a body can choose it.

**`POST /public/concierge/sessions/{session_id}/tools/{name}`** body `{call_id?, arguments?: object | JSON string}` -> the tool result. 120 calls per 10 minutes per session (429 `rate_limited`); 404 `unknown_tool`; 400 `invalid_arguments`. A repeated `call_id` returns the stored first answer with `deduped: true` and changes nothing. Every result has `status` and `followup_instructions` (English; send them with `response.create`), and may have `ui`: `{kind: request|handoff|tracker|order, token?, order_id?}` or `{kind: emergency, numbers: [{number, kind}]}`. A refusal from Service or Upsell comes back as a 200 result (`status: rate_limited|error`, `code`), never a 500.

| Tool | Arguments | Result |
|---|---|---|
| `notify_staff` | `request_type` (Service key or `other`), `description_en`, `label?`, `amenity_item_id?`, `quantity?` (1-20), `original_text?`, `language?`, `priority?` (low/medium/high), `sensitivity?` (none/handoff/sensitive), `scheduled_for?`, `category?` (legacy), `room?` (room-less surfaces only) | `{status: created|merged|escalated|room_required|limit, request_ref, merged, verified_room, ui:{kind:'request', token}}`. `service.api.create_request` with the session's room and the surface's source; the guest's own words are appended to the English description. An unverified room adds "Room not verified: please confirm at the door" (one wording for web and phone). `sensitivity=sensitive` goes to `service.api.escalate_to_human(sensitive=True)` (words not copied). 15 requests per session. |
| `escalate_to_human` | `reason` (unknown_answer, complaint, refund_or_billing, legal, emergency, asked_for_human, other), `summary_en`, `urgency?`, `original_text?` | `{status: escalated, request_ref, reason, ui:{kind:'handoff', token}}`. `unknown_answer` also records the question in `cc_kb_unknowns`. `emergency`: an L1 `emergency` request through `service.api.create_request` (Service opens the incident) and `ui:{kind:'emergency', numbers}`; a second emergency within 30 s in the session is `deduped`. |
| `get_request_status` | `request_ref?` | `{status: ok|none, requests:[{kind, ref, request_ref, label, state, state_text, cancelled, token}] or [{kind:'order', ref, order_ref, status, items}], ui:{kind:'tracker'}}` (`request_ref` / `order_ref` repeat `ref` under the key every other tool result uses, so the phone model reads one key). This session's requests; for a verified room also the same room's other verified sessions in the last 36 h. Never staff names. |
| `get_menu` | `outlet_key?`, `category?` | `{status: ok|unavailable, items:[{item_id, name, section, outlet, price, currency, dietary, description}], truncated}` from `upsell.api.get_menu` (orderable now). |
| `place_order` | `lines:[{menu_item_id, qty}]`, `confirm`, `outlet_key?`, `notes?`, `room?` (room-less surfaces) | `confirm=false`: `{status: quoted, quote_id, lines, price_display, subtotal, taxes, total, currency, suggestions}` via `upsell.api.quote_for_session` (re-priced from the database, plus-plus fees shown). `confirm=true`: needs a quote for the same basket from this session within 10 minutes (else `quote_required`), then `upsell.api.confirm_order` -> `{status: placed, order_ref, total, currency, verified_room, upsell?, ui:{kind:'order', token, order_id}}`. Not on WhatsApp (`not_available`). |
| `search_hotel_info` | `query` | `{status: ok|not_found, results:[{kb_ref, text}]}`: FTS over approved entries. |

Eval sessions (`surface=eval`) run every tool as a dry run: realistic answers (a real quote, rolled back), `dry_run: true`, `DRY-` refs, nothing filed.

**`POST /public/concierge/sessions/{session_id}/turns`** body `{role: user|assistant, text, item_id?, language?}` -> `{ok, stored, safety:{level, kind}, escalated, ui?, followup_instructions?, request_ref?}`. 400 per 10 minutes per session. Stored in `conversation_logs` (with `prompt_version`, language, `consent_ref`, consent flags, `safety_level`; a repeated `item_id` is stored once) only when the guest allowed recording. Guest turns run `service.api.classify_safety` synchronously; an emergency with no emergency escalation in the session in the last 30 s makes the server file one (`escalated: true`) and return `ui:{kind:'emergency', numbers}` with the follow-up to speak.

**`POST /public/concierge/sessions/{session_id}/chat`** body `{message (<= 4000), language?, stream?}` -> `{text, handoff, tool_calls:[{name, status, ui}], ui, safety, prompt_version}`. 60 per 10 minutes per session. The text model (`AI_MODEL_CONCIERGE_TEXT`) runs with the session's server-built prompt (a voice surface's session uses the `text_fallback` variant) and executes tools server-side (at most 4 rounds). Both turns go through the turn log above, so an emergency is escalated before the model answers. With `stream: true` or `Accept: text/event-stream`: `data: {"type":"delta","text"}` pieces, then `data: {"type":"done", ...the JSON reply}`. AI off: a hand-off sentence with `handoff: true`.

### WhatsApp (GUEST_PRIORITY 30, `concierge.whatsapp_chat`)

A text from a known guest (`guests.api.is_known_guest_sender`) to the hotel's number, concierge enabled. Without `ai_processing` + `transfer_abroad` consent the guest gets one interactive question (the current `CONSENT_TEXT_VERSION` wording in the language the message's script points to, any guest language, English for Latin script; signed button ids `ccwa:yes|no:<session>:<lang>`); messages sent meanwhile wait and are answered after "Yes". "No" records the refusal, hands the guest to a person (`service.api.escalate_to_human`) and later messages pass to Service's fallback. The reply is written by task `concierge.whatsapp_reply` (enqueued in the webhook's transaction) and sent as hotel WhatsApp text inside the 24 h window. One `cc_sessions` row (surface `whatsapp`, transport `none`) per sender per 2 hours.

### Console (`/hotels/{slug}/concierge`, members only)

| Method and path | Permission | Body / response |
|---|---|---|
| `GET /kb` | view | `{kb_mode, sections[], missing_required[], has_legacy_text, imports[], open_unknowns}` |
| `GET /kb/sections` | view | `[{id,key,title_en,title_ar,sort,required,approved,pending}]` |
| `GET /kb/entries?status=&section_id=&q=` | view | entry rows |
| `POST /kb/entries` | manage | `{section_id? or section_key?, kind, question_en?, answer_en, question_ar?, answer_ar?, tags[], fact_key?, fact_value?, location_slug?, source_url?, source_label?, valid_from?, valid_to?, approve?}` -> 201 entry (pending unless `approve`) |
| `PATCH /kb/entries/{id}` | manage | any entry field, `approve?`; editing approved text makes it pending (version + 1) unless `approve` |
| `POST /kb/entries/{id}/approve` | manage | entry; archives the previous approved entry for the same `fact_key` |
| `POST /kb/entries/{id}/reject` | manage | `{reason?}`; pending only (409 `kb_entry_not_pending`) |
| `POST /kb/entries/{id}/archive` | manage | withdraws it from guests |
| `GET /kb/entries/{id}/revisions` | view | `[{id,entry_id,version,change,snapshot,actor_type,actor_id,at}]` |
| `GET /kb/imports` / `POST /kb/imports` | view / manage | `{source: legacy_text|text, text?}` -> 202 import row (409 `no_legacy_text`) |
| `POST /kb/imports/{id}/approve` | manage | approves its pending entries, sets `kb_mode=structured`; 409 `kb_import_not_done` |
| `PUT /kb/mode` | manage | `{kb_mode}` |
| `GET /kb/unknowns?status=open` | view | `[{id,title,samples[],languages[],count,count_7d,first_at,last_at,status,kb_entry_id}]` |
| `POST /kb/unknowns/{id}/answer` | manage | `{answer_en, question_en?, answer_ar?, question_ar?, section_key?, approve?}` -> 201 entry; resolves the `concierge.kb_gap` signal |
| `POST /kb/unknowns/{id}/dismiss` | manage | unknown row |
| `GET /live` | view | `{live, source: setting\|grandfathered\|demo\|default, readiness: trust.api.readiness()}` |
| `PUT /live` | manage | `{live: bool}` -> `{live, source, overridden}`; 409 `readiness_not_met` (ON only) |
| `GET /transcripts?room=&source=&flagged=&before=&limit=` | view | `{conversations:[{id, session_id, room, source, language, started_at, last_at, turns, guest_turns, safety_level, preview}], next_before, visibility: full\|none}`. Conversations (`conversation_logs` grouped by `session_id`), newest last turn first, at most 100 per page (`limit` default 50); pass `next_before` back as `before` (a bad cursor restarts from the top). `source`: web_room, web_location, phone, text_fallback, whatsapp or legacy (old guest-page rows), else 400 `invalid_source`. `flagged=true`: only conversations with an emergency, sensitive or handoff turn; `safety_level` is each conversation's worst. `preview` is the first guest message (140 chars) with phone numbers and emails shortened even for full readers. Masked server-side (pii class `transcript`): owner, gm and front desk read it; any other role that holds `concierge.view` gets `preview: null` and `visibility: none` |
| `GET /transcripts/{id}` | view | `{conversation:{...list fields, surface, verified, prompt_version, ended_at, end_reason}, turns:[{id, role, message, language, safety_level, source, item_id, at}], tools:[{name, status, outcome, request_ref, order_ref, at}], visibility}`. Turns in order (at most 1000), words whole for a full reader and `message: null` otherwise; `tools` are the session's concierge tool calls without their arguments (those hold the guest's words). 404 `conversation_not_found` (also for another hotel's id) |
| `GET /prompt-preview?surface=&language=&room=&location=` | manage | `{surface, prompt_version, model, voice, kb_mode, greeting_text, instructions, tools, kb_entry_ids}` |
| `POST /kb/test {question (<=500), language: en\|ar, entry_id?}` | manage | one dry-run answer on the eval surface (nothing filed, no turn logged): `{text ([KB:n] tags stripped), prompt_version, kb_entry_ids, cited, tool_calls[{name, status}], entry_included}`; 409 `ai_disabled` when AI is off (Wave 1 integration, `router.py`) |

Entry validation codes: `answer_required`, `source_url_required` (local places), `invalid_fact_key`, `invalid_fact_value`, `invalid_kind`, `unknown_section`, `kb_entry_archived`.

## Jobs and signals

- Task `concierge.kb_import` (payload `{import_id, text?}`): AI split through `clefs.ai.structured` when on, paragraph split otherwise; invalid fact values are kept as plain text.
- Scheduled `concierge.session_gc` (every 30 min): closes expired sessions.
- Signal `concierge.kb_gap` (service_risk, weekly): 5 or more unknowns in one cluster in 7 days; resolved when the question is answered or dismissed.
- Scheduled `concierge.unknowns_cluster` (hourly, per hotel): merges open unknown clusters that the insert-time match missed (rapidfuzz, then one `clefs.ai.structured` grouping by meaning when AI is on); merged rows get `status=merged, merged_into`; re-checks `concierge.kb_gap` on the survivors.
- Scheduled `concierge.chat_history_gc` (every 30 min): clears `cc_sessions.chat_history` of ended sessions.
- Task `concierge.whatsapp_reply` (payload `{session_id, inbound_id, use_pending}`): the WhatsApp answer.
- Scheduled `concierge.upsell_performance` (Mondays 07:00 hotel time) and signal `concierge.upsell_performance` (money_available, weekly): 20 or more pairing suggestions shown in quotes in 7 days and under 5% then ordered in the same session.

## Metrics

All `concierge.view`; engaged guest sessions only (not evals or prewarms; at least one turn or tool call). Dimensions `surface`, `language`, `verified`.

| Key | Unit | Measure |
|---|---|---|
| `concierge.sessions` | count | conversations |
| `concierge.containment_rate` | percent | no staff request, hand-off, emergency or order |
| `concierge.unknown_rate` | percent | at least one unanswered question |
| `concierge.handoff_rate` | percent | handed to a person or an emergency |
| `concierge.orders` | count | orders placed through `place_order` |
| `concierge.order_revenue` | money (hotel currency) | their totals at the time of ordering |

`upsell_accept_rate` and `upsell_revenue` (03 spec 2.8) are Upsell's (`upsell.upsell_accept_rate`, `upsell.upsell_revenue`), which owns the funnel.
