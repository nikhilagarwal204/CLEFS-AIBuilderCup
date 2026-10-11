# Trust (`trust`)

Owned by lane **H Trust + Marketing** from Wave 1. The foundation created this stub; only that lane edits this directory (plus `tests/modules/trust/`).

- Migrations: `migrations/NNNN_trust_<desc>.sql|.py`, numbers 0800-0849 (`python -m clefs.migrate new --module trust "desc"`). The privacy part uses 0800-0819; evals and pilots use 0820-0849.
- New tables start with `trs_`.
- Console routes live under `/hotels/{slug}/trust/` (`router.py`); guest and lead routes under `/public/`, device routes under `/device/`, webhooks under `/webhooks/`.
- Permissions are declared in `module.py` and must start with `trust.`.
- `api.py` is this module's public surface. Other modules may import ONLY `clefs.modules.trust.api`; its signatures are frozen (see `clefs/CONTRACTS.md`). Implement the bodies, never change the signatures.
- Need something from a frozen foundation file? Work around it locally and leave a `# FOUNDATION-REQUEST:` comment.

## What the privacy part builds (03 spec 5.1-5.3)

| File | What |
|---|---|
| `audit_view.py` | Audit viewer over the foundation `audit_log`: filters, keyset paging, field diff, CSV |
| `chain.py` | Hash-chain verification (`trust.audit_chain_verify`, 03:30 hotel time; weekly full walk) and signal `trust.audit_chain_break` |
| `pii_classes.py` | Trust's PII field classes, registered into `clefs.core.pii` at import |
| `consent_texts.py` + `0801` | Platform consent wording (every purpose, English and Arabic, version `clefs-2026-10`) |
| `dsr.py`, `subject.py` | DSR queue, verification and executors (access, portability, erase, restrict, object, rectify) |
| `retention.py` | Retention policies, `trust.retention_purge` (02:30 hotel time), `trust.retention_purge_platform` (02:45 UTC), `trs_deletion_log` |
| `tenant_export.py` | Owner-only zip of all hotel data through `clefs.files`, 7-day link (task `trust.tenant_export`) |
| `public.py` | Public privacy request form endpoints |

Tables (`0800_trust_privacy.sql`): `trs_audit_chain_state`, `trs_dsr_requests`, `trs_subject_flags`, `trs_retention_policies`, `trs_deletion_log`, `trs_exports`. `0802_trust_dsr_flood.sql` adds `trs_dsr_requests.suspected_flood`.

Signals (all `compliance`, `daily`, never in the daily top 5): `trust.audit_chain_break`, `trust.retention_purge_failed`, `trust.dsr_due`.

### Permissions

| Permission | Roles | Gates |
|---|---|---|
| `trust.view` | owner, gm | the Trust tab, retention list and log, PII matrix, consent texts |
| `trust.audit.view` | owner, gm | audit viewer, CSV export, chain status and verify |
| `trust.privacy.manage` | owner, gm | the DSR queue |
| `trust.manage` | owner | retention changes and runs, consent wording, tenant export, benchmark opt-in |
| `trust.evals.view` | owner, gm | scenarios, runs, results, budget, the readiness gate (`concierge.manage` may also read the gate) |
| `trust.evals.manage` | owner, gm | custom scenarios, manual runs, auto-KB sync |
| `trust.readiness.override` | owner | overriding a red readiness gate (audited) |
| `trust.pilots.view` | owner, gm, finance | pilots, scorecards, the ROI report, benchmarks |
| `trust.pilots.manage` | owner, gm | creating, editing and moving pilots, manual checkpoints |
| `trust.benchmarks.admin` | none (Clefs admins only) | benchmark cohorts and snapshots (platform data) |

### PII field classes added to the matrix

Core rows (`guest_name`, `guest_contact`, `health`, `transcript`, `staff_phone`) are the foundation's. Trust adds (columns owner, gm, finance, revenue, dept_head, front_desk, staff/device):

| Class | Cells | Masked renders as |
|---|---|---|
| `guest_id_document` | masked, masked, none, none, none, full, none | last 3 characters |
| `guest_birth_date` | full, full, none, none, none, full, none | n/a |
| `payment_card` | masked, masked, masked, none, none, masked, none | `•••• 1234` (never full) |
| `request_text` | full, full, none, none, own department, full, full | n/a |
| `audit_ip` | full, masked, none, none, none, none, none | `203.0.x.x` |

`tests/modules/trust/test_pii_matrix.py` asserts every cell of every row, core and Trust, through `trust.api.mask`.

Fixes made in `clefs/core/pii.py` (review findings correctness-2, security-privacy-5, ux-conventions-1):
- "Own department" and F&B compare canonical keys (`pii.canonical_department`): Service's "Maintenance" matches a team "Engineering", "Front Desk" matches "Front Office" or "Reception", every F&B spelling is `fnb`.
- `masked` phones reveal the country code and at most the last 3 digits (`pii.mask_contact_phone`); a national number shows 3 of 10 digits instead of 8.
- `first_name()` reads Arabic script and the Arabic comma (U+060C), drops Arabic and Latin honorifics, keeps compound given names (`Abdul Rahman`, `عبد الله`) and never returns a family name (`Al Mansoori Fatima` gives `Fatima`, `Al Mansoori` gives nothing).

### Module API added (`api.py`)

- `is_restricted(cur, hotel_slug, subject_ref, *, scope="marketing") -> bool`: true after a DSR restrict/object (scoped) or erase (all scopes). A per-channel scope (`marketing_whatsapp`, `marketing_email`, any `marketing_*`) is also covered by a `marketing` flag. `subject_ref` is `privacy.contact_ref` hex, as in `consents`. Restrict/object/erase also withdraw the matching consents, so `core.consent.has_consent` callers are covered already.

- `readiness(cur, hotel_slug) -> dict` (frozen signature, now built): see "Evals and the readiness gate" below.

## What the evals and pilots part builds (03 spec 5.4-5.5)

| File | What |
|---|---|
| `evals/dsl.py` | The expectations DSL (strict pydantic: a typo fails the YAML sync) |
| `evals/textcheck.py` | Language by Unicode script ratio, sentence count, amounts in the hotel's currency (AED, the yen, any ISO code), whole-token numbers (Arabic-Indic and full-width digits folded), the em dash ban |
| `evals/graders.py` | Deterministic graders over a transcript |
| `evals/judge.py` | LLM judge (`clefs.ai.structured`, task `trust.eval_judge`, model `AI_MODEL_TRUST_EVAL_JUDGE`), given the hotel's approved knowledge with the transcript; retried after a failed call, then fails closed |
| `evals/standard/*.yaml` | The standard set: unknowns, price promises, off-policy, emergencies (the hotel country's numbers: 998 and 997 in the UAE, 119 in Japan), routing, multilingual (Gulf Arabic, Russian, Hindi, Arabizi), prompt injection (incl. the room override and "who is in 502"), refund/legal |
| `evals/scenarios.py` | Scenario store: YAML sync (hotel NULL), custom scenarios |
| `evals/autokb.py` | Auto-KB suite: 2 paraphrases per approved entry in English, plus 2 in Arabic when the entry has Arabic (`trust.eval_paraphrase`) |
| `evals/runner.py` | Runs a scenario through `concierge.api.build_surface_session(surface="eval", dry_run=True)` and `respond_text`; falls back to the Responses API with the same prompt and tools while the text turn is not built; flaky scenarios decided by best of 3 |
| `evals/runs.py` | Runs (task `trust.eval_run`, nightly `trust.eval_nightly` 04:00 hotel time), the daily cost cap, regressions |
| `evals/watch.py` | `trust.eval_watch` (every 5 min): KB or prompt change, 10-minute debounce, then a run |
| `evals/readiness.py` | The readiness gate and owner overrides |
| `pilots/store.py` | Pilots, criteria, baselines (through `clefs.core.metrics`) |
| `pilots/checkpoints.py` | `trust.pilot_checkpoint` (07:00 hotel time) and its signals |
| `pilots/report.py` | ROI report data: Clefs-verified profit per available room-night and per room |
| `pilots/benchmarks.py` | Opt-in cohorts, monthly snapshots (`trust.benchmark_snapshot`, the 3rd at 05:00 UTC), bands only below 5 hotels |

Tables: `0820_trust_evals.sql` (`trs_eval_scenarios`, `trs_eval_runs`, `trs_eval_results`, `trs_eval_watch`, `trs_readiness_overrides`) and `0821_trust_pilots.sql` (`trs_pilots`, `trs_pilot_checkpoints`, `trs_benchmark_optins`, `trs_benchmark_cohorts`, `trs_benchmark_snapshots`, `trs_benchmark_positions`). Eval cost is kept in micro-USD (`cost_micros`, Canon K3) with `cost_usd` generated beside it; the conversion price is BIGINT minor units plus currency.

Signals: `trust.eval_regression` (service_risk, daily; a safety flip is severity 4), `trust.pilot_at_risk` and `trust.pilot_criteria_met` (verified, daily).

What the eval runner expects from Concierge (through `concierge.api` only):
- `build_surface_session(hotel, surface="eval", room, language, dry_run=True, cur=)` returns the guest's exact spec with `prompt_version`.
- `respond_text(spec, messages=[{role, content}])` returns `TextTurn.tool_calls` items shaped `{name, arguments (dict or JSON string), result}`, where `result` is the dry-run executor's output. The graders read `result.room` (room binding), `result.severity` (`L1` for the stuck lift and hot-weather AC) and `result.ack_minutes` / `result.resolve_minutes` (extra bed 5/30); a key the executor does not return is skipped, never failed. `implemented=False` switches the run to the Responses fallback.
- Optional `kb_sections(cur, hotel_slug) -> [{key, approved}]` for the gate's section check; without it the gate matches section keywords in approved `kb_facts` rows (legacy paragraphs never count).
- The text turn's AI calls should use tasks starting `concierge.text` or `concierge.respond`, so the daily cap sees them.

Settings read through `env()`: `TRUST_EVAL_DAILY_CAP_USD` (default 5), `AI_MODEL_TRUST_EVAL_JUDGE`, `AI_MODEL_TRUST_EVAL_PARAPHRASE`, `AI_MODEL_TRUST_EVAL_TURN`.

## HTTP API

Errors are `{detail, code}`. Times are ISO 8601 with offset. Console routes need the permission shown; every mutation is audited (`trust.*` actions).

### Audit viewer (`trust.audit.view`)

| Method and path | Request | Response |
|---|---|---|
| `GET /hotels/{slug}/trust/audit` | query `actor_type, actor_id, action` (exact, or a prefix ending in `.` or `*`), `entity_type, entity_id, from, to` (`YYYY-MM-DD` = hotel-local day, `to` inclusive; or ISO datetime with offset, `to` exclusive), `q` (text in action, entity id/type, actor label), `limit` (1-200, 50), `before_id` | `{items: [{id, hotel_slug, at, actor_type, actor_id, actor_label, action, entity_type, entity_id, ip, request_id, meta, has_diff}], next_before_id}` newest first; `ip` masked to the network for a GM. 400 `invalid_date` |
| `GET /hotels/{slug}/trust/audit/facets` | | `{actions[], entity_types[], actor_types[]}` (from the latest 20k rows) |
| `GET /hotels/{slug}/trust/audit/{id}` | | the full row plus `before, after, prev_hash, row_hash` (hex), `hash_ok`, `diff: [{path, op: added\|removed\|changed, before, after}]`; 404 `audit_entry_not_found` |
| `GET /hotels/{slug}/trust/audit/export.csv` | same filters | `text/csv` attachment (UTF-8 with BOM), at most 50,000 rows, columns `id, at, actor_type, actor_id, actor_label, action, entity_type, entity_id, ip, request_id, changed_fields, before, after`; cells starting `= + - @` are prefixed with `'`. Audited as `trust.audit.export` |
| `GET /hotels/{slug}/trust/audit/chain` | | `{hotel_slug, status: unverified\|ok\|broken, last_id, last_hash, anchor_id, anchor_hash, broken_at_id, broken_reason, rows_checked, verified_at, last_full_at}` |
| `POST /hotels/{slug}/trust/audit/chain/verify?full=false` | | `{status: ok\|broken, rows_checked, last_id, broken_at_id, reason}`; a break raises `trust.audit_chain_break` |

### Retention

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/trust/retention` | `trust.view` | | `[{data_class, label, keep_days, action: delete\|anonymize, default_keep_days, min_keep_days, max_keep_days, source: builtin\|registry, tables[], overridden, note, last_run: {at, rows}\|null}]` |
| `PUT /hotels/{slug}/trust/retention/{data_class}` | `trust.manage` | `{keep_days: int\|null}` (null resets to the default) | the updated policy row; 400 `keep_days_out_of_bounds`, 404 `unknown_data_class` |
| `POST /hotels/{slug}/trust/retention/run` | `trust.manage` | | `{run_id, counts: {data_class: rows}, failures: [{data_class, error}]}` (20 s budget; the nightly job finishes the rest) |
| `GET /hotels/{slug}/trust/retention/log?limit=100` | `trust.view` | | `[{id, run_id, data_class, table_name, action, row_count, criteria: {keep_days, older_than, action}, at}]` (counts only, never contents) |

Built-in data classes: `transcripts` (365 d, delete `conversation_logs`), `request_free_text` (365 d, anonymize `staff_requests.description`, `original_text` and `guest_comment`; NULL cells and `[erased]` markers are left as they are), `device_session_logs` (180 d), `audit_log` (730 d, at least 365; verified prefix only, anchor kept), `deletion_log` (1095 d), `dsr_requests` (closed, 1095 d). Every `clefs.core.privacy.register_pii` entry is a class `<module>.<table>` with its declared retention (call audio capped at 90 d).

### PII matrix and consent wording

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/trust/pii-matrix` | `trust.view` | | `{roles[], classes: {class: {role: policy}}}` |
| `GET /hotels/{slug}/trust/consent-texts` | `trust.view` | | `[{id, hotel_slug, purpose, version, language, body, created_at, retired_at, current, scope: hotel\|platform}]` |
| `POST /hotels/{slug}/trust/consent-texts` | `trust.manage` | `{purpose, version, language="en", body}` | 201 the stored row (the hotel's version wins over the platform's); 409 `consent_text_immutable` for new wording under an existing version, 400 `invalid_consent_purpose` |

### Privacy requests (DSR queue, `trust.privacy.manage`)

`Dsr` = `{id, hotel_slug, type: access|rectify|erase|restrict|object|portability, status: received|verified|in_progress|completed|rejected|cancelled, channel, identifiers: {name, email, phone, room, reservation_ref, stay_date}, identifiers_hint: {name (first name), email, phone (masked), room, has_reservation_ref} (an object, not a string), details, language, verification: {method, note, verified_by, verified_at}, received_at, due_at (received + 30 days), days_left, overdue, suspected_flood, handled_by, completed_at, result_upload_id, result, note, updated_at}`. Identifiers and details are stored sealed and opened only here, masked by the viewer's role.

| Method and path | Request | Response |
|---|---|---|
| `GET /hotels/{slug}/trust/dsr?status=open\|all\|<status>&limit=` | | `[Dsr]` by due date |
| `POST /hotels/{slug}/trust/dsr` | `{type, identifiers: {name?, email?, phone?, room?, reservation_ref?, stay_date?}, details?, channel: console\|email\|whatsapp\|phone\|in_person, language?}` | 201 `Dsr`; 400 `identifier_required`, `invalid_dsr_type`, `invalid_channel` |
| `GET /hotels/{slug}/trust/dsr/{id}` | | `Dsr`; 404 `dsr_not_found` |
| `POST /hotels/{slug}/trust/dsr/{id}/verify` | `{method: id_document\|matched_reservation\|email_reply\|phone_callback\|in_person, note?}` | `Dsr` (status `verified`); 409 `dsr_not_pending` |
| `POST /hotels/{slug}/trust/dsr/{id}/execute` | `{note?, confirm?}` (erase needs `confirm: true`, rectify needs a `note`) | `Dsr` (status `completed`, `result` = counts per section, `result_upload_id` = the bundle for access/portability); 409 `dsr_not_verified` / `dsr_closed`, 400 `confirm_required` / `note_required` |
| `POST /hotels/{slug}/trust/dsr/{id}/close` | `{status: rejected\|cancelled, note?}` | `Dsr`; 409 `dsr_closed` |
| `GET /hotels/{slug}/trust/dsr/{id}/download` | | `{url: "/hotels/{slug}/files/{file_id}?t=...", expires_in: 604800}`; 404 `dsr_no_bundle` |

Executors: access/portability build a zip (`subject.json` + one CSV per section: stays, contacts, consents, transcripts, requests, and every registered module table that identifies the person: guest profiles, contact points, orders, messages); erase anonymizes transcripts, request text (`staff_requests.description`, `original_text`, `guest_comment`) and the stay's guest name to `[erased]`, purges the contact rows, deletes or redacts registered module rows, withdraws every consent and flags the subject; restrict flags `marketing, ai_analytics` and withdraws those consents, where `marketing` expands to every registered `marketing_*` channel purpose (guests' `marketing_whatsapp`, `marketing_email`), and, when guests is loaded, records a guests marketing suppression (`guests.api.opt_out(scope="marketing")`) for each contact given, so journeys and campaigns refuse the person even after a later opt-in; object does the same plus `profile_memory`. The consent ledger itself is kept (it holds hashed identities and is the evidence).

### Tenant export (`trust.manage`, owner)

| Method and path | Response |
|---|---|
| `POST /hotels/{slug}/trust/export` | 202 `Export` (`status: queued`); 409 `export_in_progress`. The task `trust.tenant_export` builds the zip |
| `GET /hotels/{slug}/trust/exports` | `[Export]` newest first |

`Export` = `{id, status: queued|ready|failed|expired, tables: {table: rows}, error, created_at, finished_at, expires_at, file_id, download_url}`. `download_url` is a signed `/hotels/{slug}/files/{id}?t=` path valid until `expires_at` (7 days; the file itself is purged then). The zip holds one CSV per table with a `hotel_slug` column plus `hotels.csv` and `manifest.json`; credentials, sessions, OAuth state, queue plumbing and binary columns are left out.

### Evals and the readiness gate

`Scenario` = `{id, hotel_slug (null = standard set), suite: standard|auto_kb|custom, key, category, title, surface, language, turns: [{role: "user", text}], context: {room, ...}, expectations, kb_entry_id, tags[], flaky, active, source_hash, created_at, updated_at, read_only}`. Categories: `emergency`, `injection`, `refund_legal` (the gate's safety group), `routing`, `multilingual` (the routing group), `unknowns`, `price_promises`, `off_policy`, `kb`, `general`.

`Run` = `{id, hotel_slug, trigger: manual|nightly|kb_change|prompt_change|ci, status: queued|running|completed|failed|skipped, suites[], prompt_version, kb_fingerprint, model, judge_model, runner: concierge_text|responses|none, started_at, finished_at, total, passed, pass_rate (Decimal string 0-1), category_rates: {category: {total, passed, rate}}, cost_micros, cost_usd (Decimal string), requested_by, error (cost_cap_reached | null), note, created_at}`.

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/trust/readiness` | `trust.evals.view` or `concierge.manage` | | `Readiness` (below) |
| `POST /hotels/{slug}/trust/readiness/override` | `trust.readiness.override` (owner) | `{reason}` (10-1000 chars) | 201 `{id, reason, failing[], run_id, created_by, created_at, expires_at}`; 409 `gate_already_ready`; 400 `reason_required`. Audited as `trust.readiness.override` |
| `DELETE /hotels/{slug}/trust/readiness/override/{id}` | owner | | 204; 404 `override_not_found`. Audited |
| `GET /hotels/{slug}/trust/evals/scenarios?suite=&include_inactive=` | `trust.evals.view` | | `[Scenario]` (the standard set is synced from YAML first) |
| `POST /hotels/{slug}/trust/evals/scenarios` | `trust.evals.manage` | `{key, title?, category, surface, language, turns: [str], context?, expectations, tags?, flaky?}` | 201 `Scenario` (suite custom); 400 `invalid_scenario`, 409 `scenario_exists` |
| `PATCH /hotels/{slug}/trust/evals/scenarios/{id}` | `trust.evals.manage` | any of the above but `key`, plus `active` | `Scenario`; 409 `scenario_read_only` for standard and auto-KB rows; 404 `scenario_not_found` |
| `DELETE /hotels/{slug}/trust/evals/scenarios/{id}` | `trust.evals.manage` | | 204 (deactivated; old results keep their scenario) |
| `POST /hotels/{slug}/trust/evals/auto-kb/sync` | `trust.evals.manage` | | `{entries, created, skipped, retired, errors[]}` |
| `GET /hotels/{slug}/trust/evals/runs?limit=` (1-100, default 20) | `trust.evals.view` | | `[Run]` newest first |
| `POST /hotels/{slug}/trust/evals/runs` | `trust.evals.manage` | `{suites?: [standard|auto_kb|custom], note?}` | 202 `Run` (queued; task `trust.eval_run` executes it); 409 `eval_run_in_progress` |
| `GET /hotels/{slug}/trust/evals/runs/{id}` | `trust.evals.view` | | `Run` + `{previous_run_id, previous_pass_rate}`; 404 `eval_run_not_found` |
| `GET /hotels/{slug}/trust/evals/runs/{id}/results?failed_only=` | `trust.evals.view` | | `[{id, run_id, scenario_id, scenario_key, suite, category, passed, score, attempts, transcript: {messages[], tool_calls[], runner, implemented, handoff, error}, tool_calls: [{name, arguments, result}], grader_outputs: [{grader, passed, detail, skipped?, score?, criteria?}], failure_reasons[], title, language, turns}]`, failures first |
| `GET /hotels/{slug}/trust/evals/budget` | `trust.evals.view` | | `{cap_usd, spent_today_usd, remaining_usd}` (Decimal strings; the day is hotel-local) |

`Readiness` = `{ready, overridden, checks: [{key, label, ok, detail}], run_id, prompt_version, kb_fingerprint, evaluated_at, override: {id, reason, failing, run_id, created_by, created_at, expires_at} | null}`. Check keys: `kb_sections` (wifi, checkout, breakfast, pool, parking, emergency, pets, smoking approved), `recent_run` (completed on the current prompt_version and KB fingerprint, at most 7 days old), `safety` (100%), `routing` (95% or above), `overall` (90% or above). An override lapses after 7 days or when a newer run completes. `trust.api.readiness(cur, hotel_slug)` returns the same dict.

Expectations DSL keys (all optional): `must_call [{name: str|[str], args_match, result_match}]`, `must_not_call [str|{name, args_match}]`, `must_escalate {reason, urgency} | true`, `must_not_escalate`, `must_contain_any [str]`, `must_not_contain [str]`, `language str|[str]`, `max_sentences`, `no_unlisted_prices` (amounts in the hotel's currency), `emergency_numbers [str] | {country: [str]}` (a map keyed by the hotel's ISO country; a country it does not list fails), `grounded_in_kb {entry_id} | true`, `bound_room`, `unknown_or_escalate`, `rubric [str]`. A matcher is a scalar (case-insensitive, numeric when both parse), a list (any of) or `{contains|gte|lte|any}`. `result_match` keys are checked only when the dry-run executor returned them. The em dash ban applies to every scenario.

### Pilots and the ROI report

`Criterion` = `{metric_key, comparator: gte|lte|gt|lt|improve_pct|reduce_pct, target (Decimal), weight (default 1), label}`; `improve_pct` and `reduce_pct` compare with the baseline value. `metric_key` must be a registered metric (400 `unknown_metric`).

`Pilot` = `{id, hotel_slug, name, modules[], start_date, end_date, status: draft|active|completed|converted|cancelled, success_criteria: [Criterion], baseline_start, baseline_end, baseline_metrics: {metric_key: value}, conversion_price_minor, conversion_price (Decimal string), currency, terms: {billing_period: month|year, on_track_threshold (0-1, default 0.7), ...}, outcome, created_by, created_at, updated_at}`. Status moves draft -> active -> completed -> converted, and any open state -> cancelled (409 `illegal_transition` otherwise); completing freezes `outcome`.

`Evaluation` = `{period_start, period_end, criteria: [{metric_key, label, comparator, target, weight, value, baseline, change_pct, met, status: met|not_met|no_data}], score (weighted share met, Decimal string), all_met}`.

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/trust/pilots` | `trust.pilots.view` | | `[Pilot]` |
| `POST /hotels/{slug}/trust/pilots` | `trust.pilots.manage` | `{name, modules[], start_date, end_date, success_criteria[], baseline_start?, baseline_end?, conversion_price? (major units, hotel currency), terms?}` | 201 `Pilot` (baseline captured now); 400 `invalid_dates`, `invalid_baseline` (both or neither, ending before the start), `unknown_metric`. Audited |
| `GET /hotels/{slug}/trust/pilots/{id}` | `trust.pilots.view` | | `Pilot` + `{scorecard: Evaluation (to today; the frozen outcome once completed), checkpoints: [{date, values, score, on_track}]}`; 404 `pilot_not_found` |
| `PATCH /hotels/{slug}/trust/pilots/{id}` | `trust.pilots.manage` | any create field | `Pilot` (criteria or baseline changes re-capture the baseline); 409 `pilot_frozen` once completed |
| `POST /hotels/{slug}/trust/pilots/{id}/status` | `trust.pilots.manage` | `{status: active|completed|converted|cancelled}` | `Pilot`; 409 `illegal_transition`. Audited as `trust.pilot.<status>` |
| `POST /hotels/{slug}/trust/pilots/{id}/checkpoint` | `trust.pilots.manage` | | `{pilot_id, date, score, on_track, all_met, signal}` (today's checkpoint now) |
| `GET /hotels/{slug}/trust/pilots/{id}/report` | `trust.pilots.view` | | `Report` (below) |

`Report` = `{pilot, hotel: {slug, name, city, rooms_count, currency}, scope: {modules, start_date, end_date, reported_through, days, baseline_start, baseline_end}, criteria[], score, all_met, final, verified: {label: "Clefs-verified profit", minor, value, actions, by_module: {module: {minor, value}}, available_room_nights, per_available_room_night_minor, per_available_room_night, per_room_minor, per_room, query}, revenue_through_clefs: [{metric_key, label, value, query}], recovery: {pilot_cost_minor, pilot_cost, baseline_cost_minor, avoided_minor, avoided}, roi: {price_minor, price, billing_period, annual_price_minor, annualised_verified_minor, roi_multiple, payback_days}, caveats[], generated_at}`. Every `query` is a body for `POST /hotels/{slug}/metrics/query`. Verified profit is the ledger only (`finance.api.verified_profit`: verified actions that count toward verified profit, by `verified_at` in the pilot dates); order and upsell revenue sit beside it and are never added to it. Available room-nights come from `occupancy_daily`, with `rooms_count` for days without a row (named in the caveats).

### Benchmarks

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/trust/benchmarks?period=YYYY-MM` | `trust.pilots.view` | | `{opted_in, items: [{cohort_key, cohort_label, metric_key, metric_label, unit, period, n_hotels, band: top|middle|bottom, p50, cutoffs, bands_only, computed_at}]}`; nothing unless this hotel opted in. `p50` and `cutoffs` are null below 5 hotels |
| `PUT /hotels/{slug}/trust/benchmarks/opt-in` | `trust.manage` (owner) | `{opted_in}` | `{hotel_slug, opted_in}`; opting out deletes this hotel's positions at once. Audited |
| `GET /hotels/{slug}/trust/benchmarks/cohorts` | `trust.benchmarks.admin` (Clefs admins) | | `[{id, key, label, criteria, metrics[], active, created_at}]` |
| `PUT /hotels/{slug}/trust/benchmarks/cohorts` | Clefs admins | `{key, label, criteria: {country, emirate, city, category, star_min, star_max, rooms_min, rooms_max}, metrics[], active}` | the cohort (upsert by key); 400 `invalid_criteria`, `unknown_metric` |
| `POST /hotels/{slug}/trust/benchmarks/snapshots` | Clefs admins | `{period: YYYY-MM}` | `[{cohort, metric_key, period, n_hotels, published}]`; fewer than 3 opted-in hotels with data publish nothing (and remove an older snapshot) |

### Public (no auth)

| Method and path | Request | Response |
|---|---|---|
| `GET /public/privacy/form?hotel=<slug>` | | `{hotel: {slug, name, guest_languages}, types[], due_days}`; 404 `hotel_not_found` |
| `POST /public/privacy/requests` | `{hotel, type, name?, email?, phone?, room?, reservation_ref?, stay_date?, details?, language?, website?}` (`website` is a honeypot and must be empty) | 202 `{ok: true, due_days: 30}` whether or not the person is known; 400 `identifier_required` / `invalid_dsr_type`, 404 `hotel_not_found`, 429 only per IP (5 per hour). Past 50 per hotel per day a request is still filed but flagged `suspected_flood` (one aggregate `trust.dsr_due` signal for all flagged rows, no receipt): the form is a legal channel and is never closed by volume. An email address gets a receipt (`trust.dsr_ack`) with no personal data in it, at most 2 per address per day |
