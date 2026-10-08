# Clefs: product, features and go-to-market

*As of 18 October 2026. This is the single current description of Clefs. It replaces "Clefs AI Concierge - Core Value Proposition & Problem-Solution Matrix.md", which predates the rebuild.*

---

## 1. Clefs in one minute

**What it is.** Clefs is software for hotels. Guests scan a QR code in their room and talk or type to the hotel's assistant, in their own language, on their own phone. Questions are answered at once from facts the hotel has approved. Anything that needs a person becomes a tracked job on the staff's screen, with a timer and an escalation path, until the guest confirms it is done.

**Who it is for.** Independent hotels and single properties of chains, typically 10 to 200 rooms, in places with many foreign guests and costly or scarce staff. Clefs is built for one property at a time; it has no chain or portfolio features.

**The lead product: the Guest Service Desk.**
- **The promise:** "Every guest request, answered in their language and tracked until it's done."
- **No app and no hardware.** It runs on the guest's own phone, from a QR card.
- **It works alongside the hotel's phones and PMS.** It replaces neither.

**What else is inside.** Clefs has 15 modules in all. Besides the Guest Service Desk there are tools for:
- direct-booking inquiries;
- Google and AI visibility;
- reviews;
- an owner's morning brief;
- OTA payout checks;
- a P&L pulse;
- demand and rate signals;
- amenity costs;
- guest messaging journeys.

They are shown as "also inside Clefs" and are not sold separately yet (section 4).

**Where it stands.**
- **The product is built and runs in production with a synthetic demo hotel.** It is "Al Rigga Crest Hotel" in Dubai, and its assistant is called "Layla".
- **There are no paying clients yet.**
- **One design partner.** The owner of Sachika Hotels (Guwahati, India) tested a pilot run of an earlier version of the concierge and request board and gave feedback. Sachika is not a client.
- **A Dubai cold-email sprint** (Oct 2026) produced 3 replies from about 150 first emails, and 1 of those was interested.

**What happens next.** A 12-week, two-offer outreach test runs in the UK, Ireland and Australia, plus a small India test (section 8). It is judged on pilots, not replies.

---

## 2. Status snapshot

| Area | State today |
|---|---|
| Product | 15 modules plus a shared platform, built UAE-first between Sept and Oct 2026. Almost every module is "built but never run with a real hotel" (section 4 gives each one's status) |
| Production | The website and app (clefs.world) and the API run on Vercel in Singapore, with a Neon Postgres database in Singapore. The AI runs on Google Gemini (Gemini API) in the US |
| Demo | A synthetic 80-room Dubai hotel with planted cases for every module. It is reseeded on the morning of a demo, and guards stop it messaging anyone |
| Real-world use | The design partner tested a pilot run of the earlier concierge and request board. The current version has not run with a real hotel |
| Clients | None paying |
| Outreach so far | Dubai, 9 to 16 Oct 2026. About 150 first emails: 3 replies, 6 bounces, 1 interested reply ("Sure" to the OTA money checklist). The follow-ups finish by 23 Oct. On LinkedIn, 62 invites went out on 9 Oct and 47 researched invites on 16 Oct |
| Team | Two founders, based in India; product built by one developer |

**Known gaps before a real pilot**
- **The background clock is slow.** Production runs scheduled work only about every 30 minutes, so the 3, 5 and 10-minute escalation ladder and wake-up calls can run late. A free 1-minute pinger, or Vercel Pro with per-minute cron, fixes this.
- **The hosting plan doesn't allow selling.** Vercel's Hobby plan is "non-commercial personal use only", so selling and running pilots needs a paid Pro plan. This is the founders' decision.
- **Country settings are UAE-first.** That covers emergency numbers, consent wording, the go-live safety test and fee stacks. They must be set per country before the first pilot outside the UAE or India (section 8, step 6).
- **Messaging isn't set up for real.**
  - There's no evidence yet that the Clefs WhatsApp sending number is set up, or that Meta has approved its message templates.
  - Staff tablet push alerts are log-only today, so a tablet with the app in the background won't ring.
- **Some setup has no screen yet.**
  - Nobody can reset their own password.
  - There's no QR print sheet; QR links are exported and printed outside Clefs.
  - There's no screen to record a compensation gesture (API only).

---

## 3. The lead product: the Guest Service Desk

### What the guest sees
- They scan a QR card in the room, or at a venue such as the pool or spa, and a web page opens with no app download.
- **They can:**
  - **talk** to the assistant (live voice), or **type** to it. Text is used automatically if hotel Wi-Fi blocks voice.
  - **tap** common requests (towels, water, housekeeping, maintenance).
  - **order** food and drinks from the hotel's menu, if the hotel turns that on.
  - **track** each request from received to done, and say whether it was fixed. A "no" reopens it.
  - **see emergency numbers** at once if something serious comes up.
- **Languages:** eight guest languages (English, Arabic, Russian, Hindi, Urdu, Chinese, German, French). There is no Spanish yet.
- **Consent:** the guest consents before the assistant starts, and a privacy button stops it and withdraws consent.
- **AI disclosure:** the assistant says it is an AI. The EU AI Act requires this.

### What staff see
- **One live queue** for every request, from the QR page, the phone desk, WhatsApp or staff themselves. Each request is routed to a named person by floor and department.
- **Two clocks per request:** acknowledge, then finish.
  - **Escalation:** if nobody acknowledges, the request climbs to the supervisor (3 min), then the duty manager (5 min), then the GM (10 min) by WhatsApp, SMS or email.
  - **Shadow mode:** a 7-day mode tests escalations without sending them.
- **Shared floor tablets** sign in with a staff PIN.
  - Each shift starts with a handover.
  - The tablet works offline.
  - Alarms repeat until someone acknowledges.
- **A phone-desk console** lets the front desk type up a phoned-in request in a few keystrokes. This is how Clefs fits alongside the room phone instead of replacing it.
- **Safety:** emergency and sensitive words are caught in 8 languages and go straight to the duty manager, Security and the GM. Staff have an SOS button.
- **Service recovery:** a "not fixed" answer or a complaint opens a recovery case. Goodwill gestures are checked against what each role may give.
- **Staff language:** staff read every request in English, whatever language the guest used.

### What the owner or GM sees
- Response and completion times, late and reopened rates, and guest confirmations, broken down by team, floor, shift, language and channel.
- Service risks in the morning brief.

### What the hotel gives (the trust ask)
- **What it gives:**
  - its facts: services, timings and policies, for the assistant's approved answers;
  - room for a QR card in each room;
  - a staff screen: a shared tablet or the console;
  - the names and roles of the staff who receive requests.
- **What it doesn't give:** no PMS access, no financial files, no inbox, no card data, and no phone-system change.

### How a pilot is set up (by the founders, remotely)
1. Create the hotel and its rooms. Build the assistant's knowledge from the hotel's website plus a call with the GM, and get it approved. There are 8 required sections, including emergencies.
2. Run the assistant's safety and accuracy tests until the go-live gate passes.
3. Send a print-ready QR PDF. The hotel prints the cards and places them.
4. Add staff, pair the tablets, set the escalation contacts, and send test pages.
5. Go live. Escalations leave shadow mode after the test pages.

### What it does not do (yet)
- No online payment for food orders, and no posting to the PMS folio or POS. Staff mark orders paid.
- The in-room phone AI (calling the assistant from the room phone) is built but has never been run on a real phone system.
- One avatar face is used for every hotel. By default it is a looping video animated by the voice, not a lip-synced face. A lip-sync rig exists but is switched off.
- Approved answers and consent wording exist in English and Arabic. Guests can still talk in all 8 languages.

### Price (USD list)
- **Pilot:** free for 30 days, with voice capped at 300 pooled minutes.
- **Guest Service Desk:** $3 per room per month, minimum $99.
  - Includes about 10 pooled voice minutes per room.
  - Month to month, no setup fee; annual billing gets 2 months free.
- **Text-only tier:** $1.50 per room, minimum $49. For small guesthouses and low-price markets.
- **Founding offer, first 25 hotels:** $49 a month locked for 12 months after the pilot, in return for a case study and one referral. Nothing is ever tied to a review.
- **India:** priced separately in INR, roughly Rs 2,999-3,999 a month for up to 20 rooms (an estimate).
- **Phone AI add-on:** quoted only after its live test.
- **Running cost:** about $0.12 to $1.25 per room per month of AI voice on the mini model.

### What a pilot must show
- A signed pilot letter, QR cards in rooms, and the staff screen in use.
- **Usage:** the share of stays where a guest used the assistant. The working bar is at least 1 in 6 stays; it is a judgement, not a benchmark.
- **Speed:** median minutes to acknowledge and to finish, against the hotel's targets.
- **Payment:** whether the hotel pays after 30 days.

---

## 4. Everything inside Clefs

**Status words**
- **Built:** implemented and wired in.
- **Not run live:** built but never run with a real hotel or real outside service.
- **Partial:** some pieces missing.

### Overview

| Module | What it does | Role now | What the hotel must give | Status |
|---|---|---|---|---|
| Guest page and concierge | QR voice/text assistant, requests, tracker, emergency card | **Lead** | Facts, QR placement | Built; not run live in this version |
| Service Control | Request queue, routing, timers, escalation, devices, safety, recovery, analytics | **Lead** | Staff list, a screen | Built; some screens partial |
| In-room dining and upsell | Menu, orders, add-on suggestions | Add-on from month 2-3 | Menu, kitchen workflow | Built; no payments or POS link |
| In-room phone AI | The assistant on the room phone over SIP | Add-on after a live test | Phone-system routing | Built; never run on a real phone system |
| Inquiry Guard | Direct-inquiry inbox, reply clock, quotes, payment links, verified direct bookings | Alternative (tested as offer B) | Forwarded inquiries, payment links | Built; not run live |
| Visibility | Google Hotels price and parity, Business Profile, map rank, AI answer radar | Free check only | Nothing to start; profile access to fix issues | Built; not run live |
| Reviews | One review inbox, themes, drafted replies | Feature | Nothing to draft; profile access to post | Built; not run live |
| Owner Brief | Ranked morning brief with one-tap decisions | Retention layer | Data from other modules | Built; not run live |
| Ask Clefs | Plain-language questions over the hotel's numbers | Feature | Data from other modules | Built |
| Guests and consent | Returning-guest recognition with consent | Feature | PMS exports with contacts | Built; not run live |
| Guest journeys | Pre-, in- and post-stay messages under consent rules | Parked | Guest data, approved templates | Built; never sent |
| Profit Guard | OTA statement checks, leakage findings, dispute packs, recovery ledger | Later (success fee, trusted clients only) | OTA statements, payouts, bank and PMS files | Built; not run live; UAE-specific |
| Finance (P&L pulse) | Weekly NRevPAR, monthly P&L mapped to USALI | Parked | P&L files or Xero/QuickBooks | Built; not run live |
| Demand Radar | Events, competitor prices, guardrailed rate suggestions | Feature | Rate calendar (CSV) | Built; not run live; event sources UAE-first |
| Amenity cost control | Issued amenities against expected use, in money | Parked | Store issuance logs | Built; not run live |

### Guest page and concierge
- **The guest page:**
  - per-room and per-venue QR links;
  - live voice, with text chat and automatic text fallback;
  - quick-request chips;
  - a consent sheet and a privacy button;
  - an emergency card;
  - a request and order tracker;
  - a personal greeting when the room is verified;
  - abuse and cost limits.
- **The concierge's brain:**
  - The prompt is built on the server, and the assistant only says what the hotel approved.
  - It has tools to file requests, hand off to a human, check status, show the menu and place orders.
  - A server-side safety double check, which can only raise a severity.
  - A knowledge editor with approval, plus import of existing knowledge (no PDF import yet).
  - A "not answered" queue and "test this answer".
  - Venues and transcripts.
  - A go-live switch behind a readiness gate.
  - Demo mode and metrics.
- **The concierge on WhatsApp:** answers only guests already known to the stay, and cannot take food orders.
- **Readiness gate:** 22 standard test scenarios, plus tests generated from the knowledge base and custom tests, graded by rules and an AI judge. No hotel has passed it yet; the demo bypasses it.

### Service Control
- **Intake and routing:**
  - one intake for every channel;
  - duplicate merge;
  - a per-room limit that never blocks emergencies;
  - priority and severity rules;
  - routing to a named person;
  - an understaffed alert.
- **Timers and escalation:** an acknowledge ladder and an urgent ladder, one digest per person, and WhatsApp, SMS and email delivery.
  - It is late by up to 30 minutes until the background clock is fixed (section 2).
- **Floor tablets:** pairing, PIN lock, shared sessions, handover, offline work, and repeating alarms.
  - Background push is log-only today.
  - The staff interface is English only.
- **Safety:** word lists in 8 languages, an AI check that can only raise severity, incident records, a staff SOS, and emergency numbers for the guest. Numbers exist for the UAE and India only today.
- **Phone desk:** keyboard-first filing, room lookup, a duplicate guard, and a "caller wants status" path. Booking calls go to Inquiry Guard.
- **Guest tracker:** a private link, a "resolved?" question, and a reopen on "no".
- **Service recovery:** automatic cases, a LEARN checklist, and an authority-limit editor.
  - There is no screen yet to record a gesture or list cases.
- **Roster and labour checks:** a weekly roster, and labour checks on save.
  - The rule values and Ramadan dates have no editing screen.
- **Analytics:** response, completion, late and reopened rates by team, floor, shift, language and channel, plus brief items.
- **Classic board:** the original staff dashboard still works on top of the new engine. Its "open access" mode for older hotels must be switched off before any real rollout.

### In-room dining and upsell
- Guest menu on the room page, with ordering priced on the server against the hotel's fees.
- A menu console with CSV import (no PDF import yet).
- Staff phone orders.
- Order handling with orders marked paid by hand.
- Pairing and upgrade suggestions, with a funnel from shown to accepted to paid.
- Late-checkout and spa offers are not built.

### In-room phone AI
- **What's there:** PBX setup recipes, extension-to-room mapping, call admission with operator fallback, transfer to the front desk, emergency handling, and a local test harness.
- **What isn't proven:** it has never run against the Gemini Live API phone path or a real phone system. Transcripts need an always-on worker that the current hosting lacks.

### Inquiry Guard (the "Direct" alternative)
- **Intake:** the website form, WhatsApp, email, Meta and Google ad lead forms, and phone-desk logging.
- **Handling:**
  - automatic reading and routing;
  - duplicate merge;
  - a reply clock counting business hours;
  - an estimate of money at risk;
  - AI reply drafts;
  - quotes inside the owner's rate limits;
  - a guest quote page;
  - gateway or manual payment links.
- **Verified direct bookings:** read from the hotel's reservation data. OTA "leak" detection runs inside Booking.com's 30-day data window.
- **Limits:**
  - Never used by a real hotel.
  - The payment adapters have only run against test fakes.
  - AI reading and drafting stay limited to website-form inquiries until counsel approves wider use.
  - Quotes carry UAE tax lines today.

### Visibility
- **Google Hotels:** whether the hotel's own website price appears, parity against the OTAs, and organic position.
- **Search and website:** a map rank grid, a Business Profile connection with performance and keywords, a lodging-facts sync, and site health.
- **AI answer radar:** weekly questions to AI answer engines, with mention rate, share of voice, cited sources and wrong-fact detection.
- **Free Google check:** a public lead magnet. It is UAE-only today.
- **Limits:** AI answers from the API can differ from the consumer apps, and Perplexity stays off until its terms are approved.

### Reviews
- One feed from Google (via the Business Profile, or SerpApi if opted in), Tripadvisor, CSV imports and forwarded review emails.
- Themes and severity by department, with links to the request behind a complaint.
- Policy-checked reply drafts, kept for 30 days.
- Posting to Google waits on Clefs' Business Profile API approval; until then, replies are copied and pasted by hand.

### Owner Brief and Ask Clefs
- **Owner Brief:**
  - a ranked daily list of decisions carrying money or service risk, plus weekly and monthly editions;
  - delivered by WhatsApp with buttons, by email, or on a private mobile page;
  - overdue escalation and quiet hours;
  - a weekly verified-value report.
  - Meta has not yet approved its WhatsApp templates.
- **Ask Clefs:** plain-language questions over approved metrics, with every number checked against the hotel's data. It works on the web and WhatsApp, and only answers what registered metrics cover.

### Guests, consent and journeys
- **Guest recognition:**
  - Contacts are stored as keyed hashes, and profiles exist only with consent.
  - Returning guests are recognised.
  - Consent is captured on the registration card, with STOP and opt-out handling.
  - It has never run on a real PMS export.
- **Guest journeys (J1 to J8):** confirmation, planning, offer, check-in, a day-one check, a check-out recovery gate, a feedback request, and rebooking.
  - Every template is owner-approved.
  - Consent, time-window and frequency rules apply, and there is no review gating.
  - Nothing has ever been sent, and there's no screen to connect a hotel's own WhatsApp number.

### Profit Guard and the free OTA audit (UAE-specific today)
- **Reading the files:** imports OTA statements, payouts, virtual-card reports and bank files, including by email forwarding. It matches them to PMS stays and bank credits and reads contract terms.
- **Findings:** 15 leakage checks, each with a confidence score that learns from dismissals.
- **Recovery:** dispute packs for owner approval, and a recovery ledger that counts money only when a credit shows up. There's also a virtual-card tracker, net ADR by channel, and a pilot scorecard.
- **UAE extras:** Tourism Dirham and fee checks, with a monthly remittance ledger.
- **Free OTA audit page:** a public lead magnet with a UAE Leakage Index. The index stays hidden until there are 30 audits.
- **Limits:** every OTA, payout, bank and PMS column map is unverified against real exports. Clefs never files a claim with an OTA itself.

### Finance (P&L pulse)
- A weekly room-revenue pulse that needs no P&L.
- A monthly P&L or trial balance mapped to the USALI standard, with AI proposals and owner confirmation.
- Pre-publish checks, anomaly alerts, and a "Clefs-verified profit" panel.
- Xero and QuickBooks connectors, never run on a real ledger. Zoho is a stub.

### Demand Radar
- An event calendar from several sources with change history, and relevance scoring.
- A 90-night demand heatmap, competitor prices labelled as indicative, and rate suggestions inside the owner's floor and ceiling.
- It never changes rates itself.
- **Follow-up drafts:** staffing, pre-arrival offers, FAQ updates and campaign exports.
- The curated event calendar and some sources are UAE-only today.

### Amenity cost control
- An item catalogue, issuance logging and CSV imports.
- Variance and spike alerts, cost per occupied room, minibar items not billed, par levels and stock counts, and a single-use-plastic flag.
- It is only as good as the hotel's issuance logging.

---

## 5. Platform, trust and hosting

- **Accounts and roles:**
  - Roles are owner, GM, finance, revenue, department head, front desk and staff.
  - Sign-in is by password, plus one-tap links for decisions.
  - Shared tablets use PIN shifts.
  - New accounts are created by a Clefs admin (CLI). Self-service password reset is not built.
- **Signals, actions and the autonomy dial:**
  - Every finding is a ranked signal with an amount and evidence.
  - Every recommendation becomes a tracked action.
  - The owner sets how far Clefs may act on its own, per action type. Money-moving actions always need approval.
- **Messaging:** one outbox for WhatsApp (Meta Cloud API), email (Resend, both directions) and SMS, with retries and WhatsApp's rules built in.
- **Data intake:**
  - Files and forwarded emails are turned into clean records, with AI column matching and PDF extraction.
  - There's no live PMS, channel-manager or extranet connection.
  - Uploads over about 4 MB fail on the current hosting.
- **Trust and privacy:**
  - An audit log with a nightly tamper check.
  - Separate, versioned consents for each use of guest data.
  - Guest contacts hashed or encrypted, with personal details masked by role.
  - A 30-day privacy-request queue, nightly retention purges, and an owner data export.
  - Public pages: `/security`, `/dpa`, `/subprocessors` and a privacy notice. Counsel has not reviewed them yet.
- **Pilot scorecard and ROI report:** success criteria and a baseline written before day one, and a printable report where every figure links to its query.
- **Hosting:**
  - Two Vercel projects (web and API) in Singapore and Neon Postgres in Singapore. AI processing runs on Google Gemini (Gemini API) in the US.
  - Every module can be switched off system-wide or per hotel.
  - There is a Docker Compose option for in-country hosting if a contract requires it.
- **Demo hotel:** synthetic data with planted cases for every module, guards against real-world effects, and logins for every role.

---

## 6. Research verdict (October 2026)

The research ran nine passes on:
- hotelier demand surveys;
- guest-side competition;
- owner-side competition;
- trust and adoption;
- markets in Asia, the Middle East and Africa;
- markets in Europe and the Americas;
- USD pricing;
- how today's leaders entered;
- a deep dive on the AI front desk.

Then came a weighted scoring, two skeptics, a final judge, and a fact-check of 60 claims against their sources.

**What hotels need most**
- **Labour.**
  - Top financial pressures for US owners and operators were supplies (71%) and labour costs (65%). More than half were understaffed (AHLA, 246 respondents, Feb 2026).
  - 72.2% of Japanese lodgings report shortages.
  - Greece's hospitality vacancy rate was 8.0%, against 3.2% for the EU.
- **OTA dependence.**
  - OTAs took 63.4% of independent-hotel bookings in Cloudbeds' 2025 data, and 29.9% in Europe (HOTREC).
  - High commission is the most-cited complaint about booking platforms: 49% in TakeUp's survey of nearly 200 small US independents.

**Competition**
- Every category Clefs covers is crowded. Hotel Tech Report lists:

  | Category | Products |
  |---|---|
  | Guest messaging | 89 |
  | Guest apps | 94 |
  | Staff collaboration | 63 |
  | Revenue management | 78 |
  | Reputation | 62 |
  | Voice and call centre | 16 |

- The real gaps are narrow:
  - no vendor checked sells a talking concierge on the guest's own phone;
  - little checks whether each inquiry booked direct or went to an OTA;
  - amenity leakage has no vendor, but no evidence of demand either.

**Niche or one-stop.** Niche. The leaders checked each entered with one narrow job and became suites years later. Small owners buy all-in-one mainly from their PMS vendor.

**The choice.** Three finalists came out roughly tied:
- **Guest Service Desk.** The founders' pick. It asks the least trust, has a format no checked vendor sells, and speaks to labour pressure.
- **Clefs Direct** (inquiry-speed test, then Inquiry Guard). The research judge's pick. It speaks to OTA dependence and reports results in dollars.
- **Missed-Booking Line.** The phone AI answering only reservation calls nobody picks up. It is untested.

The Guest Service Desk leads. Direct is tested alongside it, under a switch rule (section 8).

**What the data says about common beliefs**

| Belief | What the data says |
|---|---|
| "Hotels want an all-in-one platform from us" | No. Lead with one job and keep the rest as "also inside" |
| "Owners need to trust us before sharing financial files" | Yes. Keep OTA statements and P&L out of the first ask |
| "Indian owners don't understand AI" | Not supported. Lack of AI knowledge is the top barrier in Europe too (39%), and 94% of Indian travellers are open to AI. Cheap staff and USD pricing are likelier reasons. The India test logs the stated reason for every no |
| "Owners will be wowed by the AI" | The wow gets the meeting; numbers get the contract. Hotel leaders' top AI goal is cutting cost and improving productivity (34%), with guest experience at 9%. Lead with outcomes, not "AI" |
| "Guests prefer humans" | Partly. In a 3,250-traveller survey, 66% prefer a human welcome. Clefs answers simple things instantly and routes the rest to people |
| "Hotels won't give up their phones" | They don't have to. Clefs works alongside the room phone, and the phone desk logs phoned-in requests into the same queue |

---

## 7. Positioning and the website

- **One global English site, prices in USD,** with India priced separately in INR.
- **Headline promise:** "Every guest request, answered in their language and tracked until it's done." There is no "AI" in headlines; it appears in "How it works", the FAQ and the disclosure.
- **"No new hardware, no PMS change, keep your phones."**
- **The other modules** sit in a short "Also inside Clefs" block with no prices. It includes a "Direct bookings" section, so offer B lands fairly.
- **Calls to action:**
  - "Book a 15-minute demo" (Calendly).
  - "Hear the concierge" (the recorded greeting).
  - There is no public live-voice demo, because it would cost AI minutes per visitor. Prospects who reply get a private demo built for their own hotel.
- **The security page** says what the Guest Service Desk never needs: your PMS, financial files, your inbox and card data. A data-processing agreement with EU standard clauses is available on request.
- **The design partner** is named only with the owner's written OK, as a "design partner", never "customer".

---

## 8. Go-to-market plan (October 2026 to January 2027)

### Step 0: close out Dubai
- Answer the one interested reply.
- Let the last follow-ups finish (23 Oct), record the final numbers, then stop Dubai sending.

### Markets

| Now | Why | Email rules |
|---|---|---|
| UK + Ireland (800 hotels) | Year-round, costly staff, English | UK: limited companies only, with your identity, an opt-out and a privacy link; sole traders need consent. Ireland: companies unless they objected; individuals need consent |
| Australia (600) | Highest ability to pay, summer season | Email only addresses a hotel publishes itself, about its business, with a clear sender and unsubscribe (Spam Act inferred consent) |
| India (150, test cell) | Direction on Indian demand; tests the "AI understanding" belief | Priced in INR |

- **Later:**
  - the Maldives, Malaysia and Bali, after the first pilots;
  - Greece in Jan-Mar 2027, before its season;
  - Portugal after 3 paying hotels.
- **Avoid for now:**
  - the US (saturated; only about a third of adults use WhatsApp);
  - Spain (unrequested promotional email is banned);
  - Italy (B2B email needs consent);
  - Japan, Korea and Thailand (other messaging apps, low English);
  - Brazil and Mexico (would need Portuguese or Spanish selling).

### Offers (first email, alternated in every market)
- **A, Guest Service Desk:** "Want to hear [Hotel]'s own concierge answer a guest in [language]? Reply yes."
  - The founders build a private demo from the hotel's public website for those who reply.
- **B, Direct:** "Reply 'test' and within 5 working days I'll send your team one realistic guest inquiry and time the first human reply."
  - The owner agrees first.
- **Rules for every email:**
  - under 80 words, with two hotel-specific details;
  - follow-ups on day 3 and day 7;
  - an opt-out on every message;
  - never ask for files in a first email.

### Lists and sending
- **Lists:** about 1,550 independent hotels of 10 to 200 rooms, built from OpenStreetMap, which is free. Business emails come from each hotel's own website.
- **Sending:** from the free Gmail account, up to about 40 new hotels a day on weekdays, with a 5% bounce stop.
- **LinkedIn** by hand alongside.
- **Tracking:** the workbook tracks every hotel by market, offer and stage (replied, demo, pilot, paying), plus what each prospect asked about.

### What counts as a pilot
A signed pilot letter, plus:
- **Guest Service Desk:** QR cards in rooms and the staff screen in use.
- **Direct:** the hotel forwarding its inquiry inbox.

A speed-test result is not a pilot.

### Decision gates
- **Week 4:** stop an offer with no interested replies after 300 hotels.
- **Week 8:**
  - 4 or more pilots live: continue.
  - 2-3: extend 4 weeks.
  - 1 or fewer across both offers: the channel failed, not the product, so spend 4 weeks on warm introductions before changing the product.
  - **Switch rule:** Direct takes the lead only if its offer has at least 3 pilots and at least 2 more than the Guest Service Desk.
- **Week 12:** the Guest Service Desk is validated if at least 3 hotels pay and at least 1 in 6 stays used the assistant. If none of the first 3 pilots pays, change the product, in this order: Direct, then the Missed-Booking Line.
- **Sizing:** at Dubai's 0.7% interested-reply rate, 1,550 hotels give about 1-2 pilots; at 1.5%, 3-4. The plan assumes beating Dubai.

### Getting paid
- Stripe is invite-only for Indian businesses, and Razorpay takes international cards only from registered businesses.
- Start as a sole proprietorship with PayPal, and add Razorpay once registered (check with a CA).

### Before the first pilot outside the UAE and India
- **Country settings:**
  - local emergency numbers;
  - a country-aware safety test;
  - the guest privacy notice and consent wording;
  - fees zeroed or ordering off;
  - unused modules off;
  - escalations live after test pages.
- **Paperwork:** a data-processing agreement with EU standard clauses and the UK addendum, stating where data is stored (Singapore) and processed (US).
- **Hosting:** the background clock and Vercel plan decisions (section 2).

---

## 9. GTM ideas backlog

**Proof and trust**
- Get a written quote and an introduction from the design partner (with permission).
- Publish one pilot case study with numbers, and a "what we never access" page.
- Build a Hotel Tech Report profile. Invite every paying hotel to review, with nothing tied to it: HTR bans incentivised reviews.
- Make founder-led onboarding a selling point, since slow vendor support is the most common complaint in this category.

**Channels**
- PMS marketplaces later: Apaleo first (free APIs, self-registration), then Mews, then Cloudbeds once there are 2-3 shared customers.
- Hotel associations and their vendor guides (for example AAHOA in the US, later).
- Referral partners: hospitality marketing agencies and consultants on a commission.
- Warm routes: introductions from repliers and the design partner, and LinkedIn by hand.

**Offers to test later**
- A "hear your hotel's concierge" 60-second clip in email, for resort markets.
- A free Google and AI booking check (already built, UAE-only today). It is a weak hook on its own, so pair it with a product.
- **The Missed-Booking Line:**
  - What it is: the AI answers only the reservation calls nobody picks up.
  - How to test it: first with a founder as the caller, on a free trial number if one exists.
  - What it needs: call forwarding on no answer, which is a phone-system change.
- In-room dining as the revenue add-on in month 2-3, for hotels with F&B.

**Later products, for trusted clients only**
- Profit Guard as "no recovery, no fee".
- The P&L pulse.
- Guest journeys once a hotel's WhatsApp is connected.

**Content**
- Plain-language guides: guest request playbooks, comparisons with the alternatives, and "what to ask an AI concierge vendor".

---

## 10. Risks and open questions

**Risks**
- **Guest usage is unproven.** No independent figures exist for QR concierges; the pilots decide it.
- **The category is crowded,** and PMS vendors are adding messaging. Mews launched native guest messaging on 1 Oct 2026.
- **One free Gmail account** may be rate-limited or locked mid-test.
- **Holiday timing.** Weeks 8-12 fall over the UK festive season and the Australian summer holidays.
- **Founder bandwidth** across UK and Australian hours (about 03:30 to 22:30 IST in total).
- **Real costs once selling:**
  - Vercel Pro for commercial use;
  - capped AI voice minutes in pilots;
  - WhatsApp template messages.

**Open questions**
- Will Vercel Pro be bought for commercial use, or will the founders use a free pinger only for timing?
- What does the design partner's pilot-run data show?

---

## 11. Key sources

- [AHLA survey, Mar 2026](https://www.hotel-online.com/index.php/news/rising-cost-staffing-challenges-persist-for-hotels-as-travel-demand-expected-to-hold-steady)
- [TakeUp 2025](https://www.hotelspeak.com/2025/04/takeup-unveils-first-annual-the-voice-of-independent-hospitality-2025-industry-report/)
- [Cloudbeds 2026](https://www.cloudbeds.com/articles/2026-hotels-report-reveal/)
- [HOTREC 2026](https://www.hospitalitynet.org/opinion/4134781/bookingcom-says-europes-hotels-dont-depend-on-it)
- [Canary 2026 buyers survey](https://www.hoteldive.com/news/hotel-industry-artificial-intelligence-investment-2026/815349/)
- [HES-SO 2025](https://www.hospitalitynet.org/opinion/4128078.html)
- [State of Hotel AI 2026](https://www.hospitalitynet.org/opinion/4134731/not-done-weekly-destination-ai-summit-recap)
- [Mews traveller survey, Jul 2026](https://www.mews.com/en/press/new-mews-data-guests-want-ai-in-background)
- [SiteMinder/Kantar](https://www.travolution.com/news/travel-sectors/largest-accommodation-survey-reveals-travellers-openness-to-ai/)
- [India Accommodation Barometer](https://www.hospitalitynet.org/opinion/4130834.html)
- [HTR guest messaging](https://hoteltechreport.com/guest-experience/guest-messaging-platforms)
- [HelloShift pricing](https://www.helloshift.com/pricing)
- [HiJiffy pricing](https://www.hijiffy.com/pricing)
- [Duve pricing](https://duve.com/pricing/)
- [Bookboost pricing](https://www.bookboost.io/pricing)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [UK ICO B2B rules](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/)
- [Australia Spam Act](https://www.legislation.gov.au/C2004A01214/latest/text)
- [EU AI Act Art. 50](https://artificialintelligenceact.eu/article/50/)
- [HTR review rules](https://help.hoteltechreport.com/en/articles/6399813-vendor-review-collection-guidelines)
- [Vercel fair use](https://vercel.com/docs/limits/fair-use-guidelines)
- [Stripe India](https://docs.stripe.com/india-accept-international-payments)
- [Razorpay international payments](https://razorpay.com/docs/payments/international-payments/)

The full research plan, with every gate, file path and source, is kept with the founders' working files.
