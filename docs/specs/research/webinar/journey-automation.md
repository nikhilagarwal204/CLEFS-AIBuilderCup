> Research report (webinar review), gathered 2026-09-28. Items marked UNVERIFIED were not confirmed against a primary source.

## Verdict

- **Journey: BUILD-LITE.** Build a fixed library of about 8 event-triggered steps over WhatsApp and email. It should know where each booking came from, check consent before sending, and log every send in the existing actions ledger. Do not build a campaign builder, a CRM or a referral engine.
- **Autonomy dial: BUILD.** This is cheap, it is the pattern hotel AI vendors are settling on, and it becomes Clefs' trust layer across every action type, not just messaging.
- **REJECT** the webinar's "happy guests go to Google, unhappy guests go to a private form" funnel. Google and Tripadvisor ban it explicitly (see Rules below).
- **REJECT/DEFER** a referral program. I found no primary hotel evidence for it, and marketing to guests who booked through an OTA is contractually restricted.

## 1. Evidence (mostly vendor-sourced, so treat as upper bounds)

| Claim | Number | Source |
|---|---|---|
| Confirmation email | 67.3% open rate, $86 upsell revenue per booking (2026 global) | [Revinate](https://www.revinate.com/hospitality-report/email-channel-global/) |
| Pre-arrival email | 57.7% open rate, 2.5% conversion | same |
| Post-stay survey email | 42.78% open rate on small lists; completion under 5% (NA 3.64%, APAC 4.41%); send 2–3 days after checkout | [Revinate](https://www.revinate.com/blog/hotel-surveys-performance-metrics/) |
| Pre-arrival upsell conversion | "8–12% upsell conversion without chasing" | [Duve](https://duve.com/customized-upsells-platform-for-hotels/) |
| AI vs link-based upsell | 4x conversion at The LINE SF; "up to 250%" more upsells | [Canary](https://www.canarytechnologies.com/products/hotel-upsells) |
| Surveys as a review source | 25–30% of review volume; survey score averages 83 vs 75 across all sources (May 2025). Likely inflated by who chooses to answer | [TrustYou](https://www.trustyou.com/blog/insights/go-ahead-reach-hotel-surveys-increase-review-scores-volume/) |
| Tripadvisor Review Express | 33% average uplift in reviews (2014, no method disclosed) | [Tripadvisor](https://tripadvisor.mediaroom.com/2014-04-02-Review-Express-users-see-an-average-uplift-of-33-percent-in-reviews-as-a-result-of-the-service) |
| Review Express status | Reportedly deprecated for integrations as of December 2025 (Rezdy help page; I could not open it, got a 403). **UNVERIFIED** | [Rezdy](https://support.rezdy.com/hc/en-us/articles/19867756844828-How-To-Connect-to-Tripadvisor-Review-Express-Deprecated) |
| AI message automation share | 67% of messages at Sofitel Sydney, 80% at Edgar Suites | [Duve](https://duve.com/duve-ai/) |

**Gaps:**
- **Pre-arrival preference capture results:** no primary data found. **UNVERIFIED**
- **Referral program results:** no hotel data found. The Hoxton case study is behind a form ([Mention Me](https://www.mention-me.com/hoxton-referral-programme-case-study)). **UNVERIFIED**
- **WhatsApp vs email in the GCC and India:** only vendor claims such as a "98% open rate" ([example](https://nimblebiz.ai/blog/whatsapp-open-rate-vs-email-vs-sms-benchmarks)). **UNVERIFIED.** Clefs should measure this per hotel.
- **"Send 12 days out for a 48% click-through rate":** the source I checked did not contain it. **UNVERIFIED**

## 2. Rules that constrain the design

**Google.** The policy prohibits offering incentives for reviews and says merchants must not "discourage or prohibit negative reviews, or selectively solicit positive reviews from customers." Asking for genuine reviews without incentives is allowed ([policy](https://support.google.com/contributionpolicy/answer/7400114)). Google provides an official review link and QR code under Read reviews → Get more reviews, and suggests using them in thank-you messages and at the end of chats ([help](https://support.google.com/business/answer/16816815?hl=en)).

**Tripadvisor.**
- "If any survey or external website directs users to submit a review on Tripadvisor, the user interface and experience for submitting positive and negative reviews must be identical" ([guidelines](https://www.tripadvisor.com/Trust-lvBd3L1aU38Y.html); page returned 403, text taken from the search snippet).
- Search summaries of the business-owner FAQ say sending unhappy guests to a support channel instead is a violation. I could not open that page (403). **UNVERIFIED**
- Banned incentives include raffles, discounts and upgrades ([incentives policy](https://www.tripadvisor.com/TripAdvisorInsights/w591), from search results).

**Booking.com (General Delivery Terms v2601).**
- Clause 2.9.3: the property "shall not use the Messaging Service and/or Guest contact details provided by Booking.com … to send unsolicited electronic communications."
- Clause 2.2.6: guest personal data stays on the Extranet for 30 days.
- Guest data may be processed "only to the extent necessary to perform its obligations." The clause number for this one is unclear.
- Clause 4.2.3: no manipulating reviews.
- Source: [GDT](https://admin.booking.com/hotelreg/terms-and-conditions.html?cc1=01&lang=en).
- Guest emails are aliases ending in @guest.booking.com ([partner help](https://partnerhelp.booking.com/hc/en-us/articles/115003970605-Why-does-the-guest-s-email-address-end-with-guest-booking-com-)). Secondary sources say marketing to the alias after departure is not allowed ([Hospitality Net](https://www.hospitalitynet.org/opinion/4072000.html)).

**Expedia.** Guest emails are aliases ending in @m.expediapartnercentral.com, "only valid for a short time after check-out" ([secondary](https://support.thinkreservations.com/support/solutions/articles/48000986489-expedia)). A specific marketing ban in Expedia's terms is **UNVERIFIED**.

**Meta WhatsApp template categories** ([categorization](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization)):
- A utility template must be non-promotional and specific to the user's own transaction. Mixed content counts as marketing. Utility messages "should not promote, recommend, upsell, or cross-sell."
- Since 9 April 2025, templates Meta judges to be marketing are approved as marketing automatically.

| Journey message | Category |
|---|---|
| Booking confirmation, pre-arrival information, check-in instructions | Utility. Inferred from Meta's "order management" examples; reservations are not named explicitly |
| Feedback survey tied to a specific stay | Utility. Meta says "a general/generic survey … will not be approved as utility" |
| Upsell or rebooking offer | Marketing |
| Public review request | Probably marketing. **UNVERIFIED**; test by submitting the template |

**WhatsApp pricing and limits.**
- Pricing is per message since 1 July 2025. Utility templates are free inside an open 24-hour customer-service window ([pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)).
- The UAE rate is about $0.0499 per marketing message and about AED 0.058 per utility message ([SleekFlow](https://sleekflow.io/blog/whatsapp-business-price-uae)). **UNVERIFIED** against Meta's own rate card.
- Meta caps how many marketing templates one user can receive across all businesses; blocked sends return error 131049. Messages sent inside a window opened by the guest's reply do not count. Wait at least 24 hours before retrying ([per-user limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/marketing-templates/per-user-limits/)).
- Opt-in is required and should cover each category of message; opt-outs must be honoured on or off WhatsApp ([policy](https://whatsappbusiness.com/policy/)).

**UAE PDPL (Federal Decree-Law 45/2021).**
- Article 4 allows processing without consent where it is needed to perform a contract. That covers transactional stay messages. Marketing therefore relies on consent.
- Article 17 gives an unconditional right to object to direct marketing, including profiling.
- DIFC and ADGM have their own regimes ([DLA Piper](https://www.dlapiperdataprotection.com/countries/uae-general/collection-and-processing.html)).
- Whether the Executive Regulations are now in force in 2026 is **UNVERIFIED**; sources conflict.

**Cabinet Resolution 56/2024 (telemarketing).**
- It explicitly covers "marketing SMS and social media messages."
- Rules: 9am–6pm only, no contacting numbers on the Do Not Call Register (DNCR), prior approval required. Fines run up to AED 150,000 under Resolution 57/2024 ([Pinsent Masons](https://www.pinsentmasons.com/out-law/news/uae-telemarketing-rules-ensure-businesses-operate-transparency-integrity), [Legal500](https://www.legal500.com/developments/thought-leadership/new-telemarketing-rules-and-regulations-in-the-uae-a-comprehensive-guide-to-fines-and-enforcement/)).
- Per-day or per-week frequency limits, and whether hotels messaging their own consenting past guests are exempt, are both **UNVERIFIED**. Clefs should enforce the time window on all marketing messages by default.

## 3. Sequence and caps (defaults)

| Step | Trigger | Channel / category | Notes |
|---|---|---|---|
| J1 Confirm + capture preferences | booking.created | Utility | Arrival time, bed and occasion, language (AR/EN). Ask for the guest's own contact details and marketing opt-in with separate tick boxes |
| J2 Planning info | T-7 (only if the booking was made more than 10 days out) | Utility | Planning info a week ahead ([Bookboost](https://www.bookboost.io/post/9-tips-to-structure-your-pre-arrival-communication)) |
| J3 Pre-arrival offer | T-3 | Marketing, only with consent | Bookboost recommends upsells 3–4 days out |
| J4 Check-in instructions | T-1 or morning of arrival | Utility | Directions, ID, parking |
| J5 Day-1 check | check-in + about 18h | Utility | "Anything we can fix?" A reply opens the free 24-hour window. Negative replies go to Service Control |
| J6 Checkout gate | checkout day | internal | Open service tickets alert the manager. This is the service-recovery step, and it happens before any review request |
| J7 Feedback + review links | checkout + 2 days | Utility survey. Every completion page shows the same Google and Tripadvisor links | Timing from Revinate (2–3 days) and TrustYou (within 48 hours) |
| J8 Rebooking offer | +30, +60 or yearly anniversary | Marketing, only with consent and the guest's own contact | Never to Booking.com or Expedia alias addresses |

**Caps:**
- At most 1 marketing message before arrival and 1 during the stay.
- At most 2 marketing messages per 30 days after the stay.
- At most 1 message per day unless the guest started the conversation.
- Marketing sends only 09:00–18:00 Gulf time.
- Suppress on cancellation or opt-out, and when the number is on the DNCR if Clefs can check it (UNVERIFIED how).
- Pause marketing while a complaint is open. Never pause the review request.

## 4. Autonomy frameworks, then the Clefs dial

**Prior art:**
- An academic framework defines five levels by the owner's role: Operator, Collaborator, Consultant, Approver, Observer. It treats autonomy as a design choice separate from capability ([Feng et al., 2025](https://arxiv.org/abs/2506.12469)).
- Akia: "Allow, Ask first, or Deny" per capability. Capabilities include refunds, room keycodes and tasks ([Akia](https://www.akia.com/agents/guest-messaging)).
- Duve: starts in "Suggestion Mode", then moves to automatic replies. It "stays quiet during a complaint or emergency" and escalates payment questions ([Duve](https://duve.com/duve-ai/)).
- Conduit: hands off on negative sentiment and billing, and says humans must monitor ([Conduit](https://www.conduit.ai/blog/complete-guide-to-automated-guest-messaging-how-hotels-are-resolving-95-of-inquiries-without-staff-2026)).
- Canary Agent Studio (March 2026): publishes no guardrail details ([press](https://www.canarytechnologies.com/press/hospitality-ai-agent-studio-launched)).

**Dial levels per action type:** Off → Observe (log only) → Suggest (draft) → Approve (one tap on WhatsApp) → Auto within limits → Auto.

| Action type | Default | Limits for full auto |
|---|---|---|
| Utility journey sends (J1, J2, J4, J5, J7) | Auto, once the owner approves each template | Consent or contract basis present, caps, correct language |
| Marketing offers (J3, J8) | Approve the campaign, then Auto within limits | Stock or price check, max price per offer, time window, consent, Meta error 131049 handled |
| Service-recovery gesture (amenity or credit) | Suggest | Auto only up to AED X and at most once per stay |
| Refund, folio change, rate or OTA change | Approve, always | Never auto |
| Google review reply | 4–5 stars: Suggest, can move up to Auto. 1–3 stars: Approve, always | - |
| Review request (J7) | Auto, sent to everyone | Must not depend on sentiment |
| Guest chat during an open complaint | AI stays silent and hands off to a human | - |

**Rule for full auto:** allowed only when the action is reversible or low-value, uses a pre-approved template or policy, and consent has been checked.

**Earning and losing autonomy:**
- Suggest a promotion after at least 50 decisions in 30 days with at least 95% approved without edits.
- Demote automatically on a guest complaint, an opt-out spike, a Meta quality drop, or a regression in evals.

**Ledger fields:** action_type, dial_level_at_execution, policy_version, template_id and category, consent_basis, evidence, approver, executed_at, verify_result, reversal_handle, value (AED).

## 5. Journey BUILD-LITE spec

- **Inputs:** existing reservations and stays plus `source_channel` (direct, booking, expedia, walk-in), `contact.is_ota_alias`, `consent{marketing_wa, marketing_email, granted_at, method, text_version}`, `lang`, `open_tickets`.
- **OTA-sourced guests:**
  - Send J1–J7 only, through OTA messaging or the alias while it is valid.
  - Turn guests into direct contacts by collecting their own contact details and marketing consent through the concierge QR, registration card or J5 reply.
  - Booking.com clause 2.9.3 makes marketing to OTA-provided contact details a breach.
- **Delivery:** send through Meta's WhatsApp Cloud API, using template sends and the template status and quality webhooks. Keep an Arabic/English template library pre-sorted by Meta category.
- **Rough cost** (my estimate, not sourced): an 80-room hotel with about 840 stays a month, 5 utility and 2 marketing messages per stay, comes to roughly AED 550 a month at the unverified rates above.

## 6. Defensible vs commoditized

**Commoditized:** journey messaging and upsells. Duve, Canary, Akia, Conduit, Revinate, TrustYou and PMS-native tools all have it. Clefs should not compete on template breadth.

**Defensible:**
1. Upsells and conversions to direct booking are counted in the owner's profit ledger, net of fees, and linked to commission savings found by OTA Profit Guard.
2. Compliance is built in: rules depend on booking source, OTA terms, Meta categories, UAE time windows and PDPL objection rights.
3. A service-recovery loop connects Service Control tickets to the review outcome, without gating reviews.
4. One autonomy dial covers every action type (pricing, OTA, refunds), not just chat.
