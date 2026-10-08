# Clefs demo script (UAE, 10-15 minutes)

For a live demo to **UAE hotel owners** and **chain-property GMs**, on the synthetic demo hotel **Al Rigga Crest Hotel** (`demo-deira-crest`): a 3-star, 80-room hotel in Deira, AED, Asia/Dubai. Everything on screen is synthetic, and the console header labels it "Illustrative sample". Say so at the start. Before a meeting you can show it under the prospect's name: from server/, run `.venv/bin/python ../gtm/tools/fast_reseed.py present "Their Hotel" "Area"`; refresh the whole demo with its prep, seed, wipe and push steps (about 6 minutes).

The story in one line: **"Clefs finds the money your hotel is losing or leaving on the table, tells you each morning what to do about it in AED, and keeps guest service on time, with every step on the record."**

## Before the demo (15 minutes, the same morning)

1. **Reseed on the day.** Deadlines ("charge this card within 5 days", "reply SLA breached") are relative to the seed's anchor date, so build it fresh:

   ```sh
   cd server
   export DATABASE_URL=postgresql://localhost/clefs_dev NOTIFY_MODE=log SMS_ENABLED=false ENV=development
   .venv/bin/python -m clefs.cli seed-demo --password '<one password for all demo users>'
   .venv/bin/python -m clefs.cli demo-check
   ```

   The hosted demo is the demo hotel on production (clefs.world), so refresh it with `gtm/tools/fast_reseed.py` instead: from server/, run `.venv/bin/python ../gtm/tools/fast_reseed.py` with `prep`, `seed`, `wipe` and `push` in turn (about 6 minutes in all). It builds the hotel in a local PostgreSQL 18 and uploads only its rows; between `wipe` and the end of `push` (about 3 minutes) the demo is empty, so don't run it during a meeting.

   Seed shortly before presenting, without `--anchor`: the seed then runs as of the real clock (`as_of=` in its output), so the live requests are minutes old, SLA chips count down from now and nothing reads "in 4 hours". Before 09:00 Dubai it seeds as of last night. `--anchor` seeds as of 21:00 on that day, which looks like the future when shown earlier that day.
2. **Read the `demo-check` output.** It must exit 0. It prints the brief's top 5 for today, the plants found per module and the verified value; note the top 5 so you know what the brief will open with.
3. **Keep the printed credentials:** the owner login (`owner+demo-deira-crest@example.com`), the front-desk login, two staff PINs and the device pairing code (valid 15 minutes; mint another with `python -m clefs.cli pair-code --hotel demo-deira-crest --name "Demo tablet" --kind desk` if it expires).
4. **Windows ready:**
   - A: the console, logged in as the **owner**, on `/app/demo-deira-crest/brief`.
   - B: a phone (or a narrow browser window) for the guest page `/stay/demo-deira-crest/504` and the device app.
   - C (optional): a private window for the front-desk login.
5. Locally, run the API and the worker. On clefs.world the job tick runs every 30 minutes; after a reseed, start one by hand with `gh workflow run job-tick.yml`. `NOTIFY_MODE=log` means nothing is really sent; say "in production this goes out on WhatsApp" when a message is created.
6. Close everything else. Turn off notifications. Zoom the browser to 110% for a room.

## Run of show

| # | Minutes | Screen | The point |
|---|---|---|---|
| 1 | 1.5 | Brief | One ranked list in AED each morning; decide from the phone |
| 2 | 2.0 | Profit, Findings and Disputes | OTA leakage with evidence, and a dispute packet ready to send |
| 3 | 1.0 | Profit, the credit note | Recovered money is verified from the OTA's own documents |
| 4 | 1.5 | Demand | GITEX week: pickup, comp rates, a BAR recommendation inside the owner's guardrails |
| 5 | 1.5 | Inquiries | Direct inquiries with a reply clock, AED at risk and a quote with the UAE tax stack |
| 6 | 2.0 | Service, device app, guest page in Arabic | Requests on time, on a shared tablet, in the guest's language |
| 7 | 1.0 | Visibility | What Google and AI answers get wrong about the hotel |
| 8 | 1.0 | Finance | The P&L pulse: GOPPAR, NRevPAR and what changed |
| 9 | 1.0 | Settings, Autonomy; Trust, Audit | The owner decides how much runs on its own, and every step is on the record |
| 10 | 0.5 | Close | A pilot, or the free OTA audit |

That is 13 minutes. For a 10-minute slot, skip 7 and 8 and shorten 6 to the guest page. For a GM audience, spend longer on 6; for an owner, on 2, 3 and 5.

## Opening (30 seconds, before step 1)

> "This is a synthetic demo hotel, 80 rooms in Deira. Every number you will see is demo data, but every file behind it is the kind of file your team already downloads: Booking, Agoda and Expedia statements, payouts, the bank export, virtual cards, the PMS folio, the P&L. Clefs reads those, and the only thing it asks of you is a decision."

If you want one market fact, use a registered one and say its label, for example: UAE RevPAR down 31.8% year to date through June 2026 (JLL). Nothing else from memory (see "What never to claim" below).

## 1. The owner brief (1.5 minutes)

**Screen:** Brief (`/app/demo-deira-crest/brief`), logged in as the owner.

**Show:** the daily brief's top 5, each with an AED amount, the evidence, who owns it and when it is due. The daily brief carries **money at risk only**: Profit Guard deadlines, unanswered inquiries, parity gaps, urgent service. Visibility, rank and review themes come weekly; the P&L pulse monthly.

**Do:** open the first item. Show the evidence and the state timeline. Approve one item, adjust another ("Adjust, then approve"), assign a third to the finance user.

**Say:**
> "This arrives at 7:00 on WhatsApp as a utility message with three buttons. You can approve the first item from the message, or open the brief on your phone without logging in: the link only works for these items. Nothing here is a chart you have to interpret. Each line is money, a deadline and one decision."

**For a chain GM:** "Same brief for one property. The GM decides; finance and revenue each see their own items."

## 2. Profit Guard: findings and a dispute packet (2 minutes)

**Screen:** Profit (`/app/demo-deira-crest/profit`), then Findings, then Disputes.

**Show:** findings grouped as **recoverable** (money the OTA owes back), **preventable** (a setting that keeps costing money) and **compliance** (tax items to correct). The demo has a planted case for every detector plus decoys that correctly did not fire. Good ones to open:

- **Commission on the wrong base (Booking.com):** commission charged on the municipality fee and Tourism Dirham while they are configured as charges. Booking.com charges commission on room price, service charge and VAT; the municipality fee and TD are excluded only when configured as charges.
- **No-show charged commission:** the PMS says no-show, the statement still charges commission.
- **Virtual card deadline:** a card loaded with money and a charge deadline 5 days away (a VCC must be charged within 12 months on Booking.com; Agoda UPC within 24 hours).
- **Tourism Dirham remitted short:** August TD remitted AED 350 below what the folio collected (compliance).

Open one finding and show the **trace**: which statement line, which reservation, the calculation, and the confidence.

**Do:** "Pick findings to dispute", select two Booking.com findings, open the packet: the itemised claim, the extranet text to paste, the email and the PDF ("Print version"). Approve it ("Approve packet"; disputes need the owner or finance), then "Mark as sent".

**Say:**
> "Your team does not need to find these. The work left is sending the packet, and Clefs drafts it. Every amount traces back to a line in a file the OTA sent you."

## 3. Credit-note verification (1 minute)

**Screen:** Profit, the finding list filtered to **Recovered**, or the recovery ledger on the Overview.

**Show:** the credit note `BDC-CN-202608-0033` that Booking.com issued in September. When it was imported, Clefs matched it to two open no-show findings and marked them **Recovered**, with the credit-note lines as evidence. A credit that covers less than 95% of a finding shows as a partial credit, not a win.

**Say:**
> "We only count money as recovered when the OTA's own document says so: a credit note, a statement or a payout. That is the number we would report to you in a pilot, not an estimate."

## 4. Demand Radar: GITEX week (1.5 minutes)

**Screen:** Demand (`/app/demo-deira-crest/demand`), the "Demand by night" heatmap. Scroll to December.

**Show:** GITEX Global, 7-11 December 2026 at the Dubai Exhibition Centre. Click a GITEX night to open the day drawer:

- the event and why it is relevant to a Deira hotel;
- on-the-books pace against the 2024-25 baseline (2026 history is distorted by the regional conflict, so it is not the baseline);
- the comp set's rates, up about 60% on GITEX nights, with one competitor already sold out;
- the recommendation: a BAR change inside the **owner's own minimum and maximum** (Demand, Settings, guardrails).

Point out CABSAT (5-7 October) and New Year's Eve on the same heatmap, and the Hijri holidays marked with a one-day window because they depend on the moon sighting.

**Say:**
> "Clefs recommends; it never changes your rates or your OTA settings by itself. A rate change is always an approval, from the brief or from here. After the date, it measures what happened, so you can see whether the recommendations were right."

## 5. Inquiry Guard: AED at risk and a quote (1.5 minutes)

**Screen:** Inquiries (`/app/demo-deira-crest/inquiries`).

**Show:** about 60 direct inquiries from the web form, WhatsApp, email, phone and lead forms, each with a first-reply clock. Two are **past the reply SLA**; the header shows the AED at risk, and the same line is in today's brief. A **group quote is waiting for approval**.

**Do:** open the group inquiry and its quote. Show the lines: room nights, then the UAE stack (service charge, municipality fee, VAT) and the Tourism Dirham per room per night, capped at 30 nights. Approve it, create a payment link, and open the guest's quote page (`/q/<token>`) in window B.

Then show two outcomes the demo planted:

- a **verified direct booking**: paid through an N-Genius payment link, matched to the reservation, revenue AED 800.00 and OTA commission avoided AED 139.06 gross (AED 126.45 after the payment fee);
- a **lead that leaked to Booking.com**: a web inquiry 17 days ago, then a Booking.com reservation with the same phone number four days later; Clefs puts the commission paid at AED 182.52. Three look-alikes (same name, different phone or dates, an OTA relay address) are correctly not flagged. The match stays internal, and the guest is never contacted about it.

**Say:**
> "Every inquiry you do not answer fast enough either goes to an OTA or goes nowhere. This screen tells you which, in dirhams."

## 6. Service Control: device, SLA and the Arabic guest page (2 minutes)

**Screen:** Service (`/app/demo-deira-crest/service`), then window B.

**Show on the board:** live requests by floor and department, each with its SLA clock. Point out:

- the **Friday-evening pattern**: most SLA breaches of the last months cluster on Friday evenings (a roster problem, not a people problem);
- **room 504**: the AC failed four days ago (reported on WhatsApp, fixed in 50 minutes) and again today, when the guest answered the in-stay check message (journey J5) with "AC is not cooling". That reply opened a ticket, and Clefs flags the room as a **repeat fault** for engineering. Above 40°C outside, an AC request escalates to the urgent level automatically;
- last night's noise complaint, the three urgent (L1) incidents of the period and their recovery cases.

**Do (device):** in the console, Settings, Devices, "Pair a device", then open its pairing link in window B (or `/device/demo-deira-crest` on an already paired browser). The staff PINs are the same after every reseed (`~/.clefs/demo-credentials.txt` on the founders' laptops). Pick a staff member, enter the PIN, show the handover (open requests, out-of-order rooms, VIP arrivals, notes), and acknowledge a request.

**Do (guest):** open `/stay/demo-deira-crest/504`, switch the language to **العربية**. The page turns right-to-left. Ask for two extra towels. The request appears on the board and on the device within seconds. On the guest's tracker, answer "Resolved?" with **No**: the request reopens and the team is paged again.

**Say:**
> "No app for staff to install and no personal logins on a shared tablet: the tablet is paired once, and each person starts a shift with a PIN. Escalations start in shadow mode for the first week, so you see what would have been escalated before anyone is paged."

## 7. Visibility: fact errors (1 minute)

**Screen:** Visibility (`/app/demo-deira-crest/visibility`), the AI tab, then the Google tab.

**Show:** the weekly AI answer radar. Clefs asks the questions a traveller would ask (for example, a mid-range hotel in Deira with parking) through the engines' APIs (Gemini with Grounding with Google Search, Google AI Mode through SerpApi, and Perplexity once legal has cleared its terms), and records whether the hotel is mentioned, which competitor wins, and what the answers say about the hotel. Two **fact errors** are planted:

- an answer says check-in is at 12:00; the hotel's knowledge base says 14:00;
- an answer says the hotel has no parking; the knowledge base says free parking.

Open one in the evidence drawer: the question, the engine, the answer text, the cited sources and the knowledge-base fact it contradicts, with the fix as an action for the owner.

On the Google tab, show the **parity gap**: for Friday 2 October, Google shows Booking.com at AED 412 while the hotel's official price is AED 450.

**Say:**
> "These are samples from the engines' APIs, not a guarantee of what every traveller sees, so we always show how many answers a number is based on. What we can do is find the wrong facts and fix them at the source."

## 8. Finance: the P&L pulse (1 minute)

**Screen:** Finance (`/app/demo-deira-crest/finance`), the monthly one-pager, then the trend.

**Show:** 13 months of P&L imported from the accounting export, English and Arabic account names mapped to USALI lines once and remembered. GOP, GOPPAR and NRevPAR (revenue per available room **after** OTA commission). Two planted flags:

- **July 2026:** OTA revenue booked **net** of commission, which understates both revenue and distribution cost;
- **June 2026:** labour at 31.6% of revenue, more than 4 points above its six-month average.

**Say:**
> "This is the monthly view. It reconciles to your accountant's net profit, and it connects to Xero, QuickBooks or Zoho read-only, or takes the export your accountant already sends."

## 9. Autonomy dial and audit (1 minute)

**Screen:** Settings, Autonomy (`/app/demo-deira-crest/settings/autonomy`), then Trust, Audit (`/app/demo-deira-crest/trust/audit`).

**Show:** one row per action type, each at a level from **off, observe, suggest, approve, auto within limits, auto**. Point out the defaults:

- refunds, folio, rate and OTA changes: **always approve**, whatever the dial;
- review replies: 4-5 stars start at suggest, 1-3 stars always need approval;
- guest recovery gestures: suggest, and automatic only up to an AED limit once per stay;
- utility journey messages: automatic only after the owner approves each template.

Clefs suggests a promotion after 50 decisions in 30 days with at least 95% approved unedited, and demotes itself on a complaint, an opt-out spike or a WhatsApp quality drop. Only the owner can raise a type to an automatic level.

Then the audit log: every decision, send and setting change, hash-chained so an edit or deletion would show; the banner shows the chain verified.

**Say:**
> "You decide how much runs on its own, per type of action, and you can take it back at any time. Everything it did is on this log."

## 10. Close (30 seconds)

> "The next step is to run this on your own files. The quickest start is the free OTA audit on our website: upload one OTA statement, and it reads only that file. Nothing is connected to your extranet or your PMS."

Point to `/free-ota-audit`. Do not quote a recovery figure for their hotel, and do not promise pilot terms, prices or timelines that have not been agreed. The audit counts only what their own file shows (commission on no-shows, cancellations and refunds) and lists fee and percentage lines as "to confirm with you", so it may show no AED figure at all.

## What to say, and what never to claim

The website is gated by a claims registry (`client/app/lib/claims.ts`) and a linter that fails the build on an unsourced number. The spoken demo follows the same rules, because one "where is that number from?" ends the meeting.

### Rules

1. **Market statistics only from the registry**, with their label (population, period, publisher). Allowed today:
   - Cloudbeds: 63.4% of bookings via OTAs and 36.6% direct, and cancellation rates of 21.8% (OTA) vs 10.6% (direct), both "independent hotels globally, 2025 data";
   - JLL: UAE RevPAR down 31.8%, year to date through June 2026;
   - Skyscanner: 81% of UAE travellers say they are confident using AI to plan travel (2026);
   - Microsoft: 73.3% of the UAE population aged 15-64 use generative AI (Q2 2026);
   - Booking.com: 67% "have already used AI in some aspect of travel" (global survey, 33 markets, 2025);
   - HBR: leads answered within an hour are 7 times more likely to qualify, labelled as general sales research (US firms, 2011), not hotel data;
   - DET: Dubai hotel inventory, 154,264 rooms in 827 establishments (2025);
   - Google: third-party rates for Hotel Ads and PMax Travel Goals are unavailable after 30 September 2026; free booking links are not affected (Triptease).
2. **Demo numbers are demo numbers.** Every AED amount on screen is synthetic. Say "in this demo hotel", never "hotels like yours lose AED X".
3. **Never promise an outcome.** No recovery amount, GOPPAR uplift, rank position, review volume or revenue increase for their hotel. The free OTA audit counts commission on no-shows, cancellations and refunds from their own file, and lists fee and percentage lines as "to confirm with you", with no figure; the pilot scorecard counts only verified recoveries.
4. **Say "hotels", not "independent hotels only".** The product works for a chain property's GM. There are no group, portfolio or multi-property features; do not imply them.
5. **Recommendations, not autopilot, for money.** Clefs never changes rates, OTA settings, folios or refunds on its own. Those always need a person's approval.

### Never say

- **Any webinar statistic** or anything like it, including: an 8.6M hospitality workforce gap, "46/29/14/11" discovery splits, "89% expect AI bookings", "20-40% repeat guests", "rank 41 to 1", "350 reviews in a month", missed-call percentages, "60% of front-desk calls" or "80% routine calls" or "200-400 calls a day", AI market-size figures, or GOPPAR uplift tiers. They are banned in the registry for a reason (unsourced, misquoted or a policy risk).
- **"Clefs remembers your guests"** or "guest preference memory": gated by `claims.guest_memory`, which stays false until repeat-guest recognition is live.
- **Labour-shortage framing.** UAE hotels in 2026 are a margin story, not a staffing-shortage story.
- **"Your data stays in the UAE."** The database is in Singapore and AI runs in the United States; UAE hosting is available when a contract requires it (docs/DEPLOY.md section 15).
- **"The UAE OTA Leakage Index shows ..."** It stays hidden until it has at least 30 audits behind it.
- **A total of the "If confirmed" amounts.** Lines to confirm (commission on the municipality fee and Tourism Dirham, a percentage above the default) are not money owed until the contract and the fee setup are checked with the hotel. The audit never adds them up, and neither do we.
- **That visibility numbers are what every traveller sees.** They are samples from the engines' APIs, always shown with sample sizes.
- **Review gating or review-volume promises.** Clefs sends the same review link whatever the guest's sentiment, and never asks for reviews in bursts.
- **Marketing to OTA guests.** Booking.com guest contact details are available for 30 days and may not be used for unsolicited messages. Clefs markets only to guests who gave their own contact details and consent.
- **That the Dubai 2026 Tourism Dirham relief dates are confirmed.** They ship as an owner toggle, with a banner, because the dates are unverified.
- **That WhatsApp messages are free.** Service and utility messages are billable from 1 October 2026.
- **That the in-room phone AI is live on UAE carriers.** It needs the hotel's PBX and an e& or du sign-off first.

## If something goes wrong

| Symptom | Fix |
|---|---|
| The brief is empty or shows yesterday | Reseed today (the anchor defaults to today in Dubai), then `demo-check`. |
| A deadline reads "overdue" | The seed is from an earlier day. Reseed. |
| The pairing code is refused | It expired after 15 minutes. Make another in Settings, Devices, "Pair a device" (or `python -m clefs.cli pair-code --hotel demo-deira-crest --name "Demo tablet" --kind desk`). |
| A PIN is locked | 5 wrong PINs lock a person on that device for 5 minutes. Use the other staff PIN. |
| A request does not appear live | On Vercel the console polls; wait a few seconds or refresh. Locally, check the worker is running. |
| Login fails | The password rotates on every reseed unless you pass `--password` or set `CLEFS_DEMO_PASSWORD`. |
| `seed-demo` refuses the hotel | It only rebuilds a hotel whose status is `demo`. Check the slug. |
