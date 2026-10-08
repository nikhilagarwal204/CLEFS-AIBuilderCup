# Ask Clefs (`ask`)

Natural-language questions over the governed metrics registry (02 spec 7, Canon "Ask Clefs"). The model picks KEYS (metric, dimensions, filters, period); the server validates them, runs them through `clefs.core.metrics` (bound values, hotel filter, read-only, 5 s timeout, 500-row cap) and checks every number the model writes back. There is no raw SQL anywhere in this module.

Owned by lane **G Brief + Ask**. Migrations 0750-0799, tables `ask_*`, routes under `/hotels/{slug}/ask`.

## Pipeline (`engine.py`)

1. **Rate limit**: 30 questions per hour per user (`ratelimit` key `ask:q:<user_id>`, web and WhatsApp together). Over it: 429 `rate_limited`.
2. **Guardrails** (`guardrails.py`, before any model call): guest-level personal data (names, phones, emails, passports, "who stayed in room 502", a phone number or email in the question) is `refused` with code `guest_pii`; predictions about other hotels are `refused` with `forecast_others`; empty or over 500 characters is `refused`.
3. **Catalog** (`catalog.py`): `core.metrics.list_metrics(ctx)` (each metric's own permission, so the role filters it), plus Ask's derived ratios `ask.adr` (room revenue / room nights), `ask.occupancy` (room nights / rooms x days, percent) and `ask.revpar` (room revenue / rooms x days). A ratio is visible only when its inputs are. Dimensions that would expose one guest (`guest_name`, `email`, `phone`, `reservation`, ... see `constants.PII_DIMENSIONS`) are removed.
4. **Plan** (`tools.py`, `ai.tool_call("ask.plan")`): four strict tools generated from that catalog, enums injected per hotel and role:
   - `query_metric(metric: enum, dimensions: enum[] <= 2, filters: [{field: enum, op: eq|in|gte|lte, value: string[]}] <= 5, period: {preset|null, from|null, to|null}, grain: total|day|week|month, compare_to: none|previous_period|same_period_last_year, sort: value_desc|value_asc|label_asc|time_asc, limit: 1-50)`
   - `list_metrics()`, `describe_metric(key)` (adds known values per dimension), `answer_unsupported(reason, suggestions[])`.
   - Presets: `today, yesterday, last_7d, mtd, last_month, qtd, ytd, next_30d, next_90d, wtd, last_week, last_30d, last_quarter, last_year, next_7d, next_14d`, resolved in the hotel's timezone. Explicit periods span at most 3 years. No period means month to date.
   - **At most 3 tool calls per question** across all rounds; extra calls are logged as `tool_call_budget` and not run. A refused argument goes back to the model as the tool output, so it can correct itself inside the budget.
   - Filter values are matched against the distinct-values cache (`values.py`, 10 min TTL): exact, case-insensitive, punctuation-blind, then a unique prefix ("Booking.com" -> `BOOKING`). `gte`/`lte` expand to the known values in range ("floor 5 and up").
5. **Answer** (`ai.structured("ask.answer")`) then the **grounding check** (`grounding.py`): every number in the text must match a result value, comparison, total, change percentage, difference, share of an additive total, row count, limit, a numeric dimension label, a date part of the period or buckets, or a number the owner typed, within the rounding the text implies ("AED 10.2k" for 10,200) or 0.5%. On failure it regenerates once with the offending numbers named; a second failure falls back to the deterministic summary (`summary.py`) with status `table_only`.
6. **Log**: one `ask_logs` row per question (also refusals), with the validated tool calls, the full answer, status, latency and tokens (summed from `ai_calls`).

Models: `AI_MODEL_ASK_PLAN`, `AI_MODEL_ASK_ANSWER` (else `AI_MODEL_DEFAULT`). With AI switched off a question returns status `error`, reason `ai_disabled`; saved views keep working.

## Tables (`migrations/0750_ask_core.sql`)

- `ask_logs(id, hotel_slug, user_id, channel WEB|WHATSAPP|VIEW, question, tool_calls jsonb, answer jsonb, answer_text, status answered|table_only|unsupported|refused|error, unsupported, reason, grounded, regenerated, saved_view_id, latency_ms, tokens, feedback -1|1, feedback_note, feedback_at, created_at)`. Registered as personal data (`question` is free text), deleted after 365 days.
- `ask_saved_views(id, hotel_slug, user_id, title, query jsonb, question, source_log_id, pinned, last_run_at, created_at, updated_at)`: `query` is the validated `query_metric` arguments, never SQL.
- `ask_wa_optins(hotel_slug, user_id, opted_in, method, last_asked_at, updated_at)`.

## HTTP API

All routes are under `/hotels/{slug}/ask`. Permissions: `ask.view` (owner, gm, finance, revenue, dept_head) and `ask.manage` (owner, gm). Signed-in users only (devices, legacy visitors and magic sessions never hold `ask.view`). Errors are `{detail, code}`.

**Answer** (returned by `POST /ask` and `POST /views/{id}/run`):

```
{log_id, question, status: answered|table_only|unsupported|refused|error, answer_text,
 table: {columns: [{key, label, kind: time|dimension|value|compare|change, unit?, currency?}],
         rows: [[...]], total, compare_total, row_count, limited, truncated} | null,
 chart_spec: {type: kpi|column|hbar|line, x: "bucket"|<dimension>|null, series: [{key, label}],
              unit, currency, label, data: [{x, value, compare_value} | {x, s0, s1, ...} | {value, compare_value, change_pct}]} | null,
 trace: {metric, metric_label, description, dims[], filters: [{field, values[]}],
         period: {preset, from, to, label}, grain, compare: prev_period|prev_year|null, sort, limit,
         unit, currency, row_count, truncated, derived: {numerator, denominator, scale}|null,
         source: "clefs.core.metrics"} | null,
 query: <query_metric arguments of the first result> | null,
 results: [{table, chart_spec, trace, query}],           # up to 3 queries per question
 unsupported: bool, reason: guest_pii|forecast_others|too_long|empty|model_unsupported|no_metrics|
                            ungrounded|ai_disabled|ai_error|no_tool_call|tool_call_budget|null,
 suggestions: [str], grounded: bool|null, regenerated: bool, ungrounded?: [str],
 tool_calls: int, latency_ms: int, tokens: int|null}
```

Table cells are raw numbers in major units (money in the hotel's currency, percent as 0-100); the console formats them. Change is a percentage (one decimal).

| Method and path | Access | Body | Response |
|---|---|---|---|
| `POST /hotels/{slug}/ask` | `ask.view` | `{question}` (1-2000 chars; over 500 is refused) | Answer; 429 `rate_limited` |
| `GET /ask/metrics` | `ask.view` | | `[{key, label, description, unit, currency, dimensions:[{key,label}], higher_is_better, synonyms[], derived}]` for this viewer |
| `POST /ask/{log_id}/feedback` | `ask.view`, own answers | `{value: 1|-1, note?}` | log row (below); 400 `invalid_feedback`, 404 `ask_log_not_found` |
| `GET /ask/logs?limit=20&all=false&feedback=` | `ask.view`; `all=true` needs `ask.manage` | | `[LogRow]` newest first (own, or the team's with `all=true`; `feedback=-1` lists answers marked wrong) |
| `GET /ask/logs/{log_id}` | own answer with `ask.view`, anyone's with `ask.manage`, or the magic session the WhatsApp reply minted for exactly this answer | | `LogRow`; 403, 404 `ask_log_not_found` |
| `GET /ask/views` | `ask.view` | | `[View]` (own only; pinned first, newest first) |
| `POST /ask/views` | `ask.view` | `{title, query?}` or `{title, log_id, result_index?=0}`, `pinned?` | 201 `View`; the query is validated now: 400 `invalid_view_query`, 404 `ask_log_not_found`; audited `ask.view.create` |
| `PATCH /ask/views/{id}` | owner of the view | `{title?, pinned?}` | `View`; 404 `ask_view_not_found` |
| `DELETE /ask/views/{id}` | owner of the view | | 204; audited `ask.view.delete` |
| `POST /ask/views/{id}/run` | owner of the view | | Answer (no LLM; `answer_text` is the deterministic summary, channel `VIEW`) + `view: View`; 400 `invalid_view_query` when the metric or role no longer allows it |
| `GET /ask/whatsapp` | `ask.view` | | `{eligible, role, has_phone, opted_in, updated_at, last_asked_at}` |
| `PUT /ask/whatsapp` | `ask.view`, full sessions only | `{opted_in}` | same view; 403 `ask_whatsapp_role` (not owner/gm/finance), 409 `ask_whatsapp_no_phone`; audited `ask.whatsapp.opt_in` / `opt_out` |

`LogRow = {id, channel, question, status, unsupported, reason, grounded, regenerated, answer: <Answer without log_id/tool_calls/latency>, tool_calls: [{name, arguments, ok, error?}], saved_view_id, latency_ms, tokens, feedback, feedback_note, created_at}`.
`View = {id, title, query, question, source_log_id, pinned, last_run_at, created_at, updated_at}`.

## WhatsApp Q&A (`whatsapp.py`)

- `@notify.on_whatsapp("staff", priority=60, kind="text")`: after the brief's handler (40), which takes Adjust replies and STOP/START. STOP, START, UNSUBSCRIBE and HELP are never treated as questions.
- Answers only a sender whose phone is a user opted in (`PUT /ask/whatsapp`) at a hotel where they are still an active owner, gm or finance member. Anyone else passes on to the next handler.
- Only inside the 24 h customer-service window, as a session text (`purpose="ask.whatsapp_answer"`, idempotency key per inbound message); never a template.
- Reply: the answer text, up to 10 rows as aligned lines in a code block, "+N more rows in the console", suggestions for a declined question, and a magic link (`purpose="login"`, 24 h, 3 uses, `scope={"ask_log_ids": [id]}`) to `/app/{slug}/ask?log={id}`.
- A member opted in at several hotels picks one with `"<slug>: question"`; otherwise the number's hotel, else the hotel asked most recently.

## Python API (`api.py`)

- `answer_question(cur, hotel_slug, *, user_id, question, channel="WEB") -> Answer` as that user (role-filtered, rate-limited); 403 without `ask.view`.
- `askable_metrics(cur, hotel_slug, *, user_id) -> list[dict]`: the user's catalog, `[]` without access.

## Evals

`tests/modules/ask/evals/ask_clefs.yaml` holds 67 items (54 answerable, 13 that must be refused or declined), run by `tests/modules/ask/test_evals.py` on the fake AI against a demo registry: tool-argument exact match 100% (target 90%), numeric grounding 100%, unsupported detection 100% (target 95%), no false refusals. `ASK_EVAL_LIVE=1` with a real key scores the live planner on the same file.
