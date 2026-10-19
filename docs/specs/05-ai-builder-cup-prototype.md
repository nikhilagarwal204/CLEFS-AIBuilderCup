# 05 · AI Builder Cup prototype: the guest service desk, as the deck shows it

The prototype at the deck's Final Product Link (https://clefs-632652019887.asia-south1.run.app) must show every feature of the submitted deck ("Clefs _ AI Builder Cup"): the guest asks once, in their language, from a room QR code; Gemini files the job with the right free staff member, in that person's language; the job closes only with proof. This spec fixes the design and the contracts between the parts, so the work can be built in parallel. Where an existing module already does something, extend it; do not rebuild it.

## 1. The deck, feature by feature

| Deck feature | What the prototype shows | Owner |
|---|---|---|
| Talk or type, in the guest's language, from a room QR, no app | Guest page `/stay/{hotel}/{room}?k=` with voice (Gemini Live) and text; 8 UI languages; printable room QR cards | A (guest), F (voice), D (QR cards) |
| Approved answers only | Concierge answers only from hotel-approved facts (exists: prompt + knowledge base) | A |
| Smart dispatch with ETA | Right free staff member picked; guest told "Maria from Housekeeping is on her way, about 5 minutes" | B (logic), A (wording + card) |
| Staff in their own language | Job text shown in the worker's language; worker replies (typed or voice) reach the guest in the guest's language | B (API), C (staff app), A (guest display) |
| Proof before closure | Mark as Done needs a photo; Gemini vision checks it against the request; the guest confirms the fix | B (API + vision), C (camera), A (guest confirm), D (states) |
| Automatic escalation | No acceptance: supervisor at 3 min, duty manager at 5, GM at 10; late jobs reassigned | B (logic), D (labels) |
| Safety first | Emergency words page the duty manager at once | B (paging), D (banner) |
| Recovery before the review | "Not fixed" reopens the job and opens a case the manager approves a gesture for | B (API), A (guest buttons), D (approvals) |
| Frustration detected | Gemini scores mood and frustration each guest turn; high frustration alerts the duty manager, who approves a gesture | A (scoring), B (case), D (approvals) |
| Owner view | Response times by team, floor and shift | D |
| Quality gate and shadow mode | 22 safety and accuracy tests must all pass before going live; 7 days in shadow mode | E |

The 8-step flow (slide 5) is the acceptance test: guest speaks → Gemini understands intent, items, urgency, mood → the agent creates one job per ask → dispatch with ETA → staff gets the job in their language → work done, photo taken → Gemini vision verifies → job closed, guest notified, dashboard updated.

## 2. Decisions (settled; do not redesign)

1. **Migrations.** concierge `0207`-`0209` (A), service `0131`-`0139` (B), core `0025`-`0029` (E), trust `0822`-`0824` (E). Voice relay needs none (F). Never edit an applied migration; add a new one.
2. **Mood and frustration: one design, owned by A.** Each guest text turn (and each final voice transcript) is scored by `ai.structured("concierge.mood", model=get_settings().AI_MODEL_FAST, timeout_s=3)` returning `{mood: calm|happy|neutral|confused|impatient|annoyed|angry|distressed, frustration: 0..1, urgency: low|normal|high}`, run concurrently with the main model call so it adds no latency, never blocking the reply. Stored on `cc_sessions` (`last_mood`, `max_frustration`, concierge migration 0207) and passed to `create_job` (below). Frustration >= 0.7, or mood angry or distressed, calls `service.api.flag_recovery(kind="frustration")` once per session.
3. **Jobs: new service API, frozen API untouched.** `create_request` and the other frozen functions keep their signatures. B adds the functions in section 3; A's `notify_staff` switches to `create_job`.
4. **One request can become several jobs.** `notify_staff` files one job per item or department; the prompt tells the model to call it once per distinct ask (parallel calls are fine). The guest card lists every job of the turn.
5. **"Free staff" has one definition (B, `routing.py`).** On shift now (roster), in the request's department, not off duty on their device, below `max_open`. Rank: fewest open jobs, then floor distance (room floor vs the staff member's assigned floors), then longest idle. The manager's "Choose staff" list and the ETA use the same function.
6. **ETA (B).** Minutes = base for the request type (towels or water 5, amenities 7, cleaning 20, maintenance 15, food 25, other 10) + 3 per open job ahead in that person's queue + 1 per floor of distance, capped at 45. Stored as `staff_requests.eta_minutes` and `eta_at`.
7. **Escalation ladder.** Unaccepted (not acknowledged) after 3 min: supervisor; 5: duty manager; 10: GM. Labels everywhere are "Supervisor", "Duty manager", "GM" (never L1/L2/L3, which is the emergency severity scale). At the supervisor step the job is also reassigned to the next free person, if there is one.
8. **How a job closes (no new state).** The state machine stays (`state.py`). "Mark as Done" on a job whose type needs proof uploads a photo; the server runs the vision check inside the same call: match → `complete` (state `completed`, `proof_status=verified`); mismatch → the state does not change, `proof_status=rejected`, `proof_reason` set, the supervisor is alerted, the staff app says why. Then the guest is asked "Is everything OK?": yes → `confirm` (`guest_confirmed_at`), "not fixed" → `reopen` + recovery case. A completed job with no guest answer after 30 minutes is closed with `closure_reason='no_reply'`. `closed_at` marks closure. Types that need no physical work (information, desk call, wake-up call) need no photo.
9. **Photos.** Stored in a Cloud Storage bucket with automatic deletion after 7 days when `PROOF_BUCKET` is set (production: `disco-axis-442314-k2-clefs-proof`, asia-south1, lifecycle rule delete at age 7); otherwise in Postgres (tests, local). Client downsizes to at most 1280 px JPEG quality 0.8 through a canvas (this also drops EXIF).
10. **Staff language.** The worker's reading language is `staff_members.languages[0]` (default `en`). Job text is translated once per (request, language) and cached (service migration). Staff replies are translated into the guest's language (`staff_requests.language`).
11. **URGENT** means priority high, on both the staff app and the console. Emergencies show as "Emergency".
12. **Approvals live on the service Dashboard** as a "Needs your approval" panel (recovery cases with an AI-suggested gesture: Approve, Edit, Dismiss). No new sidebar entry.
13. **"Time taken"** on the guest card counts from the guest's request until a staff member accepts (live timer, then frozen: "Accepted in 00:41"). The console card shows the same number.
14. **Languages.** The 8 UI languages that exist (en, ar, ru, hi, ur, zh, de, fr), all enabled for the demo hotel. Copy says "in your language" (the claims registry bans "any language").
15. **Assistant name** for the demo hotel is "Clefs" (the deck's screens).
16. **Owners and GMs land on the service desk**; "Response times" is the Owner view (title "Owner view: response times by team, floor and shift").
17. **Product focus.** Modules outside the deck are switched off with `CLEFS_DISABLED_MODULES` (E decides the list; service, concierge, guests, amenities, trust and core stay on). Nothing is deleted.
18. **No old-vendor words.** Never write the names of the previous AI vendor or its chatbot in code, copy, tests, docs or commit messages.

## 3. Contracts

### service.api (B implements; A, C, D call)

```python
def create_job(cur, hotel_slug: str, *, room: str, request_type: str, quantity: int | None,
               description_en: str, original_text: str | None, guest_language: str | None,
               priority: str = "normal", mood: str | None = None, frustration: float | None = None,
               session_id: str | None = None, guest_ref: str | None = None, channel: str = "concierge",
               idempotency_key: str | None = None) -> dict:
    """Create, route and dispatch one job. Returns dispatch_info(...) of the new request."""

def dispatch_info(cur, hotel_slug: str, request_id: int) -> dict:
    """{request_id, tracking_token, item_label, quantity, room, department_key, department_label,
        staff_id, staff_first_name, eta_minutes, eta_at, created_at, accepted_at, state, status,
        proof_status, guest_confirmed_at, closed_at}  (staff_* None when nobody is assigned yet)"""

def flag_recovery(cur, hotel_slug: str, *, kind: str, room: str | None, summary: str,
                  request_id: int | None = None, session_id: str | None = None,
                  guest_language: str | None = None) -> dict:
    """kind: not_fixed | frustration | never_arrived. Opens (or reuses) a recovery case with an
    AI-suggested gesture awaiting the duty manager's approval; alerts the duty manager."""

def guest_feedback(cur, hotel_slug: str, tracking_token: str, *, fixed: bool, comment: str | None = None) -> dict:
    """fixed=True: confirm (closed). fixed=False: reopen + flag_recovery(kind='not_fixed')."""

def guest_messages(cur, hotel_slug: str, tracking_token: str) -> list[dict]:
    """Staff replies and approved gestures for the guest, already in the guest's language:
    [{at, kind: staff_reply|gesture|status, text, staff_first_name}]"""
```

### REST (B implements)

Staff app (device auth, prefix `/device/service`, existing router `devices_api.py`):
- The job list and job detail add `display_title` and `display_text` in the worker's language, `original_text`, `guest_language`, `needs_proof`, `proof_status`, `proof_reason`, `eta_minutes`, `priority`, `accepted_at`.
- `POST /device/service/requests/{id}/proof` (multipart `photo`, JPEG or PNG, max 3 MB) → `{verified: bool, reason, completed: bool, request}`. A match completes the job.
- `POST /device/service/requests/{id}/reply` JSON `{text?: str, audio_b64?: str, mime?: str}` → `{text_original, text_for_guest, language}`. Audio is transcribed by Gemini.
- `POST /device/service/duty` `{on_duty: bool}` (Go Off Duty), if the existing device API lacks it.

Guest (public, tracker token, prefix `/public/r`, existing `public_api.py`):
- `GET /public/r/{token}` adds `staff_first_name`, `department_label`, `eta_minutes`, `accepted_at`, `proof_status`, `needs_guest_confirmation`, `messages` (guest_messages).
- `POST /public/r/{token}/feedback` `{fixed: bool, comment?: str}`.

Console (user auth, prefix `/hotels/{slug}/service`):
- Request rows add `proof_status`, `proof_reason`, `proof_photo_url` (short-lived signed or proxied URL), `eta_minutes`, `accepted_at`, `escalation_level` (`supervisor|duty_manager|gm|null`), `guest_confirmed_at`, `closed_at`, `closure_reason`, `mood`, `frustration`.
- `GET /hotels/{slug}/service/recovery-cases?status=open` and `POST /hotels/{slug}/service/recovery-cases/{id}/decide` `{action: approve|dismiss, gesture?: str}` (reuse the existing recovery routes if they exist; keep their paths).
- `POST /hotels/{slug}/service/requests/{id}/demo-advance` `{minutes: int}`: demo hotels only (hotel status `demo`), moves the job's clock back so the escalation ladder can be shown live.

### Concierge tool results (A)

`notify_staff` returns, per job, `ui: {kind: "request", token, card: {item_label, quantity, room, staff_first_name, department_label, eta_minutes, created_at, accepted_at, state}}`. The guest page shows a "JOB SENT TO STAFF · SENT" card per job (Guest asked for, Room, Staff member, Time taken) and the reply names the person and the ETA when known ("Of course. Housekeeping has been notified. Maria is on her way, about 5 minutes.").

### Voice relay (F)

When `LIVE_VIA_VERTEX=true` (production), `POST /public/concierge/sessions` with `transport: live` returns `{transport: "relay", relay_url: "wss://<api>/public/concierge/live/{session_id}", relay_token, model}` instead of an ephemeral token; the browser opens the relay socket and speaks the same Live protocol messages; the server holds the Vertex AI Live session with the service identity, applies the session's instructions, voice and tools, and pipes messages both ways. Without the flag the ephemeral-token path stays as is.

## 4. Demo cast (E seeds; everyone uses it)

Hotel `demo-deira-crest` (status demo), assistant name "Clefs", all 8 languages. Room 204 on floor 2. Maria Santos: Housekeeping attendant, floors 1-4, languages `fil, en` (reads Tagalog), on shift from the seed time for 14 days, with a paired staff-app device. Priya Sharma: Housekeeping attendant, floors 5-8, languages `hi, en`. A Housekeeping supervisor, a duty manager and a GM exist with role_keys supervisor, duty_manager, gm. Request types that need proof: towels, water, amenities, cleaning, turndown, maintenance, minibar, laundry.

## 5. Reviewer access (E)

A public page `/try` (linked from the homepage hero) for demo hotels only: "Be the guest in Room 204" (a keyed room link), "Open Maria's staff app" (one tap pairs the browser as Maria's device and signs her in), "Open the manager console" (one tap signs in a demo duty-manager user scoped to the demo hotel). Server routes in `server/clefs/core/demo_access.py`, answering 404 for any hotel whose status is not demo, rate-limited per IP.

## 6. Done means

Each part ships with tests (pytest for the server, `node --test` for client logic where it exists), the full server suite stays green, `tsc --noEmit` passes, and the slide 5 flow works end to end on the deployed prototype.
