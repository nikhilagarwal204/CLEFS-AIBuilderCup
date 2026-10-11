# Clefs demo script: the AI Builder Cup prototype (8-12 minutes)

The demo follows the deck. The story in one line: **hotels have more guests and fewer staff; requests get lost between phone calls, languages and shifts; Clefs gets each one done, and proves it.** "Ask once, in your language, and it gets done."

Everything runs on the demo hotel **Hotel Aozora Shinjuku** (`aozora-shinjuku`, aozora means blue sky): a fictional mid-range hotel in Nishi-Shinjuku, Tokyo, 80 rooms on 8 floors, sample data only. Say so at the start.

| What | Where |
|---|---|
| Final Product Link (the homepage) | https://clefs-632652019887.asia-south1.run.app |
| The three one-tap doors | the homepage's "Try it yourself" section (`/try` redirects there) |
| The guest in Room 204 (the room's keyed QR link) | printed by the seed, and behind the first door |
| Maria's staff app | "Open Maria's staff app" |
| The manager console as the duty manager, Yuki Sato | "Open the manager console" |

## The hotel and its cast (seeded, see `server/clefs/seed/demo_jp/core.py` and `deck.py`)

- **The hotel:** 7-18-4 Nishi-Shinjuku (fictional), about 8 minutes on foot from Shinjuku Station's West Exit and 3 from Nishi-shinjuku Station on the Marunouchi Line. Check-in from 15:00, check-out by 11:00. Breakfast buffet (Japanese and Western) at Sora Dining, 9F, 06:30-10:00, ¥2,200. Prices in yen, the 10% consumption tax included; Tokyo's accommodation tax per person per night on top.
- **The assistant** is called **Clefs** and offers 11 guest languages: English, Japanese, Korean, Chinese, Thai, Hindi, French, German, Russian, Arabic and Urdu. Its approved knowledge is in English and Japanese: the stations, Narita and Haneda, taxis, takkyubin luggage forwarding, the coin laundry, the convenience store and ATMs, tax-free shopping, quiet hours, yukata, plugs, earthquakes, 110 and 119.
- **Room 204**, floor 2. Its printed QR card opens `/stay/aozora-shinjuku/204?k=<key>`; the key makes the page "verified" for that room. Several judges can use it at once: the hotel's Service setting `guest_room_limit` is 100 requests per room per 10 minutes (the default is 5). The seed leaves the room clear: no open job or emergency on it when the demo starts.
- **Maria Santos**, Housekeeping room attendant, floors 1-4, reads Tagalog, PIN **2046**. On shift round the clock for the next 14 days, with her phone paired and her shift open on it.
- **Priya Sharma**, Housekeeping room attendant, floors 5-8, reads Hindi. On shift round the clock too.
- **Hana Suzuki**, Housekeeping supervisor (3 minutes); **Yuki Sato**, duty manager (5 minutes, emergencies, approvals); **Kenji Tanaka**, GM (10 minutes).
- The rest of the team mixes Japanese and foreign staff, each reading jobs in their own language: Vietnamese, Nepali, Indonesian, Filipino and Chinese, across Housekeeping, Front Desk, Maintenance, In-Room Dining, Concierge and Security.
- Console logins (printed by the seed): owner, gm, finance, revenue, front desk and the duty manager (`dutymanager+aozora-shinjuku@example.com`, Yuki Sato, role department head, this hotel only).

Room 204 towels go to Maria: the free attendant nearest the room, ranked by fewest open jobs, then floor distance (05 spec, decision 5). If Maria still holds an open job, the next one goes to Priya, and the guest page says so. Close jobs as you go, or reseed.

## Before the demo (10 minutes)

1. **Reseed** so the demo starts clean: open jobs from earlier visitors are cleared, the roster restarts its 14 days, and the 22 tests are queued again. The `clefs-seed` Cloud Run job runs `python -m clefs.cli seed-demo`, which builds the Tokyo hotel as of the real clock in Tokyo:

   ```sh
   gcloud run jobs execute clefs-seed --region asia-south1 --wait
   ```

   Its log (Cloud Console: Cloud Run, Jobs, `clefs-seed`, the latest execution, Logs) prints the console logins, the staff PINs and, under "deck demo", the keyed Room 204 link. Reseeding does not change the room's key, so a printed Room 204 card keeps working. Do not reseed during a meeting: it wipes the demo hotel first.

   Recommended: let Cloud Scheduler reseed every morning, Tokyo time, so reviewers always find a fresh desk (not set up yet):

   ```sh
   gcloud scheduler jobs create http clefs-reseed --location asia-south1 \
     --schedule "15 9 * * *" --time-zone "Asia/Tokyo" --http-method POST \
     --uri "https://run.googleapis.com/v2/projects/disco-axis-442314-k2/locations/asia-south1/jobs/clefs-seed:run" \
     --oauth-service-account-email <a service account allowed to run clefs-seed>
   ```

   **Once, when switching to the Tokyo hotel:** retire the old UAE demo hotel, so its guest page, doors, staff app and logins stop answering (a status change; no row is deleted, and `--restore` undoes it):

   ```sh
   gcloud run jobs execute clefs-seed --region asia-south1 --wait \
     --args="-m,clefs.cli,retire-demo,--hotel,demo-deira-crest"
   ```

2. **The minute tick.** Cloud Scheduler's `clefs-tick` calls `/internal/cron/tick?budget_s=25` on `clefs-api` every minute. It drives the escalation ladder (3, 5, 10 minutes), closes unanswered completed jobs after 30 minutes, and, after a reseed, runs the 22 safety and accuracy tests with Gemini as the judge. Give it 3-4 minutes after the reseed, then check Trust, Quality gate in the console: **"22/22 safety and accuracy tests passed, judged by Gemini"** and **"Shadow mode: 7 of 7 days done"**. If a test failed, the gate shows which one and why.
3. **Module focus.** `clefs-api` runs with `CLEFS_DISABLED_MODULES=profit_guard,demand,reviews,brief,ask,voice,visibility,inquiries,finance` (set with the `^@^` delimiter form, since the value has commas), so the console shows the service desk, concierge, guests, amenities, menu and trust only. The Tokyo hotel switches the same modules off for itself. The `clefs-seed` job keeps every module.
4. **Screens.** One laptop on the homepage's "Try it yourself" section, and a phone for the guest if you can:
   - Guest: scan the Room 204 QR code with the phone (or the printed card), or open the guest page in a tab.
   - Staff: tap "Open Maria's staff app". It pairs this browser as Maria's phone and opens her shift; if the app shows "Who is starting?", tap Maria and enter **2046**.
   - Manager: tap "Open the manager console". It signs this browser in as Yuki Sato, the duty manager, for 2 hours, on the service desk.
5. Turn the phone's sound on (the staff app chimes on a new job). `NOTIFY_MODE=log` on the deployment: nothing is sent by WhatsApp, SMS or email; say "in production this goes out on WhatsApp".

## Run of show (deck order)

| # | Minutes | Screen | Deck slide | The point |
|---|---|---|---|---|
| 1 | 1.0 | Homepage | The problem | More guests, fewer staff; requests get lost |
| 2 | 1.5 | Guest page, Room 204 | The moment | Ask once, in your language: "Maria is on her way, about 5 minutes" |
| 3 | 1.5 | Guest page, staff app | Steps 2-5 | One job per ask, dispatch with ETA, the job in Maria's language |
| 4 | 1.5 | Staff app, guest page | Steps 6-8 | Photo proof checked by Gemini vision, the guest confirms, closed |
| 5 | 2.0 | Console, staff app, guest page | When something goes wrong | Escalation 3/5/10, a photo that does not match, "not fixed" and frustration |
| 6 | 0.5 | Guest page, console | Safety first | Emergency words page the duty manager at once |
| 7 | 0.5 | Console | Owner view | Response times by team, floor and shift |
| 8 | 1.0 | Console, Trust | Built for trust | 22/22 tests, 7 days in shadow mode, consent first, says it is an AI |
| 9 | 0.5 | Homepage | Pricing | USD 3 per room per month, free 30-day pilot |

### 1. The problem (1 minute)

**Screen:** the homepage. Hero: "Ask once, in your language, and it gets done." Scroll once: phone calls, languages, shifts. "Clefs gets each one done, and proves it."

### 2. The moment (1.5 minutes)

**Screen:** the guest page for Room 204 on the phone.

**Do:** agree to the AI processing (consent comes first, and the assistant says it is an AI), tap the mic and ask in your language, for example in Japanese, Korean or Thai: "Can we get extra towels?" (タオルを追加でもらえますか？) Or type it.

**Point to:** the reply names the person and the time: "Of course. Housekeeping has been notified. Maria is on her way, about 5 minutes." Under it, the **Job sent to staff** card: what the guest asked for, Room 204, Maria from Housekeeping, and the time taken, counting until Maria accepts.

Questions about the hotel are answered from its approved knowledge, in the guest's language: "How do I get to Haneda?", "Can you send my suitcase to the airport?" (takkyubin), "What do I do in an earthquake?"

### 3. Understanding, one job per ask, the staff member's language (1.5 minutes)

**Do:** ask for two things at once: "Two towels, and the AC is too warm." Two cards appear: Housekeeping (Maria) and Maintenance.

**Screen:** Maria's staff app. The job arrives with a chime, in Tagalog, with the guest's own words below. **Accept** it; the guest's card freezes the time ("Accepted in 00:41").

### 4. Proof before closure (1.5 minutes)

**Do:** in the staff app, **Mark as Done**, then take a photo of towels (any folded towels will do). Gemini vision compares it with the request inside the same call: a match completes the job. On the guest page: "Is everything OK?" Tap **Yes**: the job closes and the console's dashboard updates.

### 5. When something goes wrong (2 minutes)

- **Nobody accepts:** file a request and leave it. Hana Suzuki, the supervisor, is told at 3 minutes, Yuki Sato, the duty manager, at 5, Kenji Tanaka, the GM, at 10, and the job moves to the next free person (Priya). On the console the job card shows each step. To show it inside a meeting, use the console's demo control that moves the job's clock forward (demo hotels only).
- **The photo does not match:** mark a towels job done with a photo of something else. The job stays open, the staff app says why, and the supervisor is alerted.
- **The guest is not happy:** answer **Not fixed** on the guest page, or type something frustrated ("this is the third time, nobody came"). The job reopens and a recovery case appears on the console's **Needs your approval** panel with a suggested gesture. As the duty manager, **Approve** it; the guest sees the gesture on the guest page.

### 6. Safety first (30 seconds)

**Do:** on the guest page type "My child is locked in the bathroom, please help". The duty manager is paged at once (no ladder), the console shows it as **Emergency**, and the guest is told Japan's numbers: 119 for fire and ambulance, 110 for the police.

### 7. Owner view (30 seconds)

**Screen:** Service, "Owner view: response times by team, floor and shift": the same jobs the staff closed, by team, floor and shift.

### 8. Built for trust (1 minute)

**Screen:** Trust, Quality gate. The headline: **22/22 safety and accuracy tests passed**, judged by Gemini (emergencies with the hotel country's numbers, prompt tricks, prices and refunds in the hotel's currency, routing, four languages), and **7 of 7 days** in shadow mode. Going live needs both: the gate keeps the concierge off until all 22 pass, and the service desk first runs a week in which escalations are worked out and shown without messaging anyone. Open a run to show a transcript and the judge's verdict.

### 9. Close (30 seconds)

**Screen:** the homepage, Pricing. USD 3 per room per month with voice and text (at least USD 99 a month), a text-only plan at USD 1.50 per room, and a free 30-day pilot. A hotel needs QR cards, one shared staff tablet and its staff names and roles; never PMS access, financial files, card data or phone changes.

## What never to claim

- **Any language.** The hotel offers 11 guest languages. Say "in your language", never "any language".
- **A real hotel.** Hotel Aozora Shinjuku, its address, phone and team are fictional; the data is sample data.
- **The benchmark numbers as hotel results.** Voice reply 1 s, request understanding 94%, multi-request splitting 98%, translation 5/5, photo verification 91%, request to staff screen 1.2 s and task closure 60% faster were measured on the prototype in scripted tests. A hotel's own numbers come from its pilot.
- **Firestore or ADK.** The prototype stores jobs in Cloud SQL (Postgres) and calls Gemini through the Google Gen AI SDK.
- **That WhatsApp or SMS went out.** The deployment logs messages instead of sending them.

## If something goes wrong

| You see | Why, and what to do |
|---|---|
| The gate says the 22 tests are running | The tick has not run them yet after the reseed. Wait a minute and reload. |
| A test failed | Trust, Quality gate names the test and the judge's reasons. The gate stays red until a run passes all 22. |
| The towels went to Priya, not Maria | Maria still holds an open job (fewest open jobs first). Close it in her app, or reseed. |
| The staff app asks "Who is starting?" | Tap Maria Santos and enter 2046. Her shift is already open, so the PIN only puts her back at the screen. |
| A door says "Too many tries from here" | Each door is rate-limited per address and per hotel. Wait a few minutes. |
| The console door answers 409 | The demo login was given access to a hotel that is not a demo; the door refuses until that membership is removed. |
| Nothing escalates | Check that `clefs-tick` ran in the last minutes (Cloud Scheduler, `clefs-tick`, Last run). |
| The guest page cannot start voice | Type instead: typed text works without Gemini Live. |
| The old Dubai demo still answers | Retire it once (step 1 above); within a minute (API caches) its doors, guest page and logins stop. |

## Running it locally

```sh
cd server
export DATABASE_URL=postgresql://localhost/clefs_dev NOTIFY_MODE=log SMS_ENABLED=false ENV=development
.venv/bin/python -m clefs.migrate up
.venv/bin/python -m clefs.cli seed-demo --password '<one password for all demo users>'
.venv/bin/uvicorn main:app --reload --port 8000          # and in a second shell: .venv/bin/python -m clefs.worker
cd ../client && CLEFS_API_ORIGIN=http://localhost:8000 npm run dev
```

Then open http://localhost:3000. `python -m clefs.cli demo-check` checks the Tokyo hotel: the property and stays, the deck cast as its own section ("AI Builder Cup deck cast") and every planted case (Service, Amenities, Upsell), and exits 1 on any miss. The UAE demo hotel is still one flag away (`seed-demo --hotel demo-deira-crest`, every module seeded) and retires with `retire-demo --hotel demo-deira-crest`.
