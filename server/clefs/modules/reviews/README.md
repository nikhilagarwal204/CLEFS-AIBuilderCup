# Reviews (`reviews`)

Owned by lane **F1 Upsell + Reviews** from Wave 1. Built from 03 spec 4.1 and the 04 spec touchpoints (1.4 autonomy rows, F5 SerpApi, F6 OAuth, F4 inbound email, 5.4 Visibility).

- Migrations: `migrations/0600_reviews_core.sql` (range 0600-0649). Tables use the prefix `rev_`.
- Console routes live under `/hotels/{slug}/reviews/` (`router.py`). Forwarded review emails arrive through the foundation's `/webhooks/email/resend`, purpose `reviews` (handler in `jobs.py`).
- Permissions:
  - `reviews.view`: owner, gm, front_desk, dept_head.
  - `reviews.manage`: owner, gm. This is also the `decide_perm` of both reply action types.
  - Granting auto-reply consent is owner-only.
- `api.py` is the public surface. Other modules import only `clefs.modules.reviews.api`, and its signatures are frozen.

## What it does

1. **Sources** (`sources/`). Each source writes through `store.ingest`, which applies the same rules everywhere:
   - The uniqueness key is `(hotel, platform, external_id)`.
   - /10 scores are halved to /5.
   - Raw text expires after 30 days.
   - Owner responses are merged in.
   - The room hint is taken from the text.

   The sources:
   - `serpapi_google`: Google Maps through `core.serpapi.search` with purpose `reviews.sync`. It is ported from `review-intel/hotel_review_intel.py`: it finds the place once, then fetches newest first and stops at the first known review, reading at most `sync_max_pages` pages a day. It is off until the owner turns on `serpapi_enabled` (the vendor-risk disclosure), and it is skipped once GBP is connected.
   - `gbp`: the Google Business Profile v4 reviews API through `core.oauth` provider `google_business`. The location comes from `visibility.api.gbp_location` or from `config.location_name`. Replies use `updateReply`.
   - `tripadvisor`: the Content API, which returns the 5 most recent reviews. It is polled daily and the results accumulate. It needs `TRIPADVISOR_API_KEY`.
   - `csv`: presets `booking`, `agoda`, `expedia` and `generic`. Headers are matched case- and punctuation-insensitively. "Liked" and "Disliked" columns are kept, labelled.
   - `email`: mail sent to `<alias>+reviews@in.clefs.world`. It is accepted only when SPF or DKIM passed aligned with the From domain, or DMARC passed (else `sender_unauthenticated`; Resend's verdicts name no domain, so for them only DMARC counts), and then only from team members' emails, platform notification domains (exact registrable domains and their subdomains, never a look-alike), or `forward_senders`. It is capped at 200 a day. The LLM extracts the review; when AI is off, a regex fallback reads the platform and score.
2. **Analysis** (`analyze.py`) runs `clefs.ai.structured` (task `reviews.analyze`) and extracts:
   - themes from a fixed taxonomy, each mapped to a Service department (`taxonomy.py`);
   - polarity, severity and sentiment;
   - a room hint and a stay date.

   When AI is off, the keyword lexicon stands in. A deterministic safety match always raises severity to high.
3. **Service links.** A negative theme plus a room hint links to requests in `service_requests_v1` with the same room and department inside the stay window. The window is taken from a `reservations` row when one matches, else it is the stay date ±3 days, else the 14 days before the review.
4. **Negative reviews** raise the signal `reviews.negative_review`:
   - A review is negative at 3★ or below on /5 (`negative_threshold`) or at high severity.
   - Section `reviews`. Cadence is weekly, or daily when severity is high.
   - The evidence includes the drafted reply.
5. **Replies** (`replies.py`). A draft is written for each recent review that has no owner reply.
   - The draft is in the review's language, under 120 words, with no em dash, no liability admission, no public compensation offer, and an offline contact on negative replies.
   - A model draft that breaks a rule is replaced by the en/ar template.
   - Approve, post and copy are refused (422 `reply_policy`) for text that breaks a hard rule.
6. **Autonomy** (`autonomy.py`). Each draft creates one action through the dial:

   | Type | Default | Ceiling | Gate |
   |---|---|---|---|
   | `reviews.reply_positive` (4-5★) | suggest | auto | `approve` unless the review's GBP source has `auto_reply_consent` and the review is 4-5★ with no negative theme and severity none or low |
   | `reviews.reply_negative` (1-3★, or any negative) | approve | approve | |

   - `level_check` returns 409 `auto_reply_consent_required` when anyone tries to raise `reply_positive` into an auto level before a GBP source records consent.
   - Withdrawing consent lowers the dial to `approve`.
   - The executor posts through GBP when the review allows it. Otherwise it leaves the reply approved for Copy.
   - A dial execution re-checks the gate.
7. **Trends** (`trends.py`). The weekly job compares last week's negative mentions per theme with the mean of the 4 weeks before it. A rise of 50% or more with at least 3 mentions raises `reviews.theme_trend_up` (weekly). Earlier weeks' signals resolve.
8. **Competitors** (`places.py`). Places API (New) `rating` and `userRatingCount` are fetched live on every view and never stored. Only place ids are kept, in `rev_competitors`.

## Jobs, signals, metrics

- **Jobs:**
  - `reviews.sync`: daily at 05:30 hotel time.
  - `reviews.analyze`: every 15 min, 10 reviews per run, each in a savepoint, at most 3 attempts per review.
  - `reviews.raw_purge`: daily at 03:30. It nulls `raw_text`, `author_display` and `title` past `raw_expires_at` and stamps `raw_purged_at` on every expired row, including rows Trust's `trust.retention_purge` already redacted (it runs at 02:30 and does not set the flag). `rev_derived`, the rating and the dates stay. Ingest never writes raw text into a row past `raw_expires_at`, so a re-sync cannot restore it, and the API reports such a row as `purged`.
  - `reviews.theme_trends`: Mondays at 06:00.
  - `reviews.sync_now`: a task queued by the console.
- **Signals:** `reviews.negative_review` and `reviews.theme_trend_up`.
- **Metrics** (permission `reviews.view`; dimensions `platform` and `severity`):
  - `reviews.count`
  - `reviews.avg_rating_30d` (average on the /5 scale)
  - `reviews.response_rate` (%)
  - `reviews.negative_share` (%)
- **PII:** `rev_reviews.author_display`, `raw_text` and `title` are registered for redaction after 30 days.

## Public API (`api.py`)

| Function | Returns |
|---|---|
| `draft_reply(cur, hotel_slug, review_id)` | `{review_id, reply_id, text, language, needs_approval, status}`, or None for an unknown review. Analyses the review and creates the draft and its action when needed. |
| `review_counts(cur, hotel_slug, *, start, end, platform=None)` | `{total, by_platform{p: n}, by_rating{"1".."5": n}}` over hotel-local dates. `{}` when the hotel has no source and no review (on that platform), so the tile hides. |
| `reply_stats(cur, hotel_slug, *, platform='google', days=90)` | `{unanswered, response_rate (0-100, 1 dp, or None), median_reply_h (or None), total, platform, days}`. None when nothing is measured on that platform. |

## Settings (`GET/PUT /hotels/{slug}/module-settings/reviews`)

`ReviewsSettings` has these fields:
- `reply_style{signer, tone warm|formal|brief, contact}`
- `serpapi_enabled` (false), `serpapi_disclosure_accepted_by`
- `sync_max_pages` (5)
- `negative_threshold` (3.0)
- `draft_positive` (true)
- `act_on_days` (30: older reviews get no draft, signal or action)
- `competitors_enabled`
- `forward_senders[]` (addresses or `@domain`)

## HTTP API (console, `/hotels/{slug}/reviews`)

Errors are `{detail, code}`. Every write is audited as `reviews.<entity>.<verb>`. `author_display` is masked by role (core.pii `guest_name`).

**Shapes used below:**
- `Review` = `{id, platform, source_id, external_id, author_display, title, text, purged, rating, rating_scale, rating_norm_5, language, posted_at, has_owner_response, owner_response_at, room_hint, stay_date_guess, ingested_at, analyzed_at, sentiment, themes[{key, label, department, polarity, summary_en}], summary_en, severity, negative, linked_request_ids[], reply_id, reply_status, reply_needs_approval}`.
- `Reply` = `{id, hotel_slug, review_id, draft_text, language, status, needs_approval, edited, checks, action_id, approved_by, approved_at, posted_at, posted_via, platform_reply_id, model, created_at, updated_at}`.
  - `status` is one of `draft`, `approved`, `copied`, `posted`, `discarded`.
  - `posted_via` is `gbp`, `copy` or `dial`.
  - `checks` = `{ok, words, em_dash, liability[], compensation[], language_ok, has_contact, model_draft_rejected?}`.
  - `model` is `llm` or `template`.
- `Source` = `{id, hotel_slug, kind, platform, label, config, status, auto_reply_consent, auto_reply_consent_by, auto_reply_consent_at, last_synced_at, last_error, created_at, updated_at}`, plus `google{status, account_ref}` for `gbp`.
  - `kind` is one of `serpapi_google`, `gbp`, `tripadvisor`, `csv`, `email`.
  - `status` is one of `active`, `paused`, `error`, `disconnected`.

### Feed and review routes

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET ""` | view | query `platform`, `negative`, `unanswered`, `theme`, `max_rating`, `days` (90), `limit` (50, max 200), `before_id` | `[Review]`, newest first |
| `GET /summary?days=30` | view | | `{days, count, avg_rating, response_rate, negative_share, unanswered, drafts_waiting, by_platform{p: {count, avg_rating}}}` |
| `GET /themes?days=30` | view | | `[{key, label, department, negative, positive, neutral, reviews}]`, most complained-about first |
| `GET /trends?weeks=12` | view | | `{weeks[iso Monday], themes{key: {label, negative[], positive[]}}, rating[{reviews, avg_rating}]}`, buckets in hotel time |
| `GET /{review_id}` | view | | `Review` + `{model, prompt_version, source_kind, source_label, linked_requests[{id, room, request_type, department, priority, status, created_at, resolved_at}], reply: Reply \| null, postable}`. 404 `review_not_found` |
| `POST /{review_id}/analyze` | manage | | `{review_id, negative, severity, reply_id, signal_id}`. Re-runs the analysis, the draft, the signal and the action |
| `POST /{review_id}/draft` | manage | `{replace_edited?: false}` | `Reply`. Writes a new draft (a person's edit is kept unless `replace_edited`) and creates the action if there is none |

### Reply routes

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET /replies?status=draft,approved&limit=50` | view | | `[Reply]` |
| `PATCH /replies/{id}` | manage | `{text}` | `Reply`. The text is sanitised (dashes, length) and re-checked, `edited` is set and the status goes back to `draft`. 409 `illegal_transition` once copied or posted |
| `POST /replies/{id}/approve` | manage | | `Reply`. Approves the reply and its action. The executor then posts through GBP when the review allows it, else the reply stays `approved` for Copy. 422 `reply_policy` |
| `POST /replies/{id}/post` | manage | | `Reply` with status `posted`. Posts to Google now (an explicit per-reply click). 409 `not_postable` when the review is not a GBP-synced Google review or Google is not connected. 502 `vendor_error` |
| `POST /replies/{id}/copied` | manage | | `Reply` with status `copied`. The Copy button: the review counts as answered |
| `POST /replies/{id}/discard` | manage | | `Reply` with status `discarded`. The action is ignored, which moves it to `rejected` |

### Source routes

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET /sources` | view | | `{sources[Source], inbound_address ("<alias>+reviews@in.clefs.world" or null), serpapi{enabled, disclosure}, auto_reply_consent}` |
| `POST /sources` | manage | `{kind: serpapi_google\|gbp\|tripadvisor, label?, config?}` | 201 `Source`. Config keys: serpapi `{query, location}`; gbp `{location_name, account}`; tripadvisor `{location_id (digits), language}`. 409 `source_exists`; 400 `invalid_config` |
| `PATCH /sources/{id}` | manage | `{label?, config?, status?: active\|paused}` | `Source` |
| `DELETE /sources/{id}` | manage | | 204. The source's reviews stay (`source_id` becomes null) |
| `POST /sync` | manage | `{source_id?}` | 202 `{queued, job_id}`. Queues `reviews.sync_now` (sync, then analyse). 6 per hour; 429 `too_many_requests` |
| `PUT /sources/{id}/auto-reply-consent` | manage; granting is owner-only | `{granted}` | `Source`. Records who and when, a `consents` row (purpose `auto_reply`) and an audit row. 403 `owner_only`; 409 `consent_needs_gbp` |

### Import and competitor routes

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `POST /import` | manage | `{preset: booking\|agoda\|expedia\|generic, csv? \| file_id?, scale?: 5\|10, dry_run?}` | `{preset, platform, rows, created, updated, skipped, errors[{row, error}], columns{field: header}, dry_run, source_id}`. 400 `csv_missing`, `csv_columns_missing`, `invalid_preset` |
| `GET /competitors` | view | | `{place_ids[], own_place_id, updated_at, places[{place_id, name, rating, user_rating_count, maps_url, own, error?}], attribution: "Google", stored: false}`. Fetched live every time. 409 `not_configured` without `GOOGLE_API_KEY`; 60 per 10 min |
| `PUT /competitors` | manage | `{place_ids[] (max 10), own_place_id?}` | `{place_ids, own_place_id, updated_at}`. 400 `invalid_place_id` |

**Actions.** The reply actions (`reviews.reply_positive` and `reviews.reply_negative`, params `{review_id, reply_id}`) are also decided through the core routes: `POST /hotels/{slug}/actions/{id}/decide` with `reviews.manage`. A Brief or console approval runs the same executor.

## Foundation requests

- `TRIPADVISOR_API_KEY` is a `Settings` field and in `.env.example` (applied in the Wave 1 integration).
