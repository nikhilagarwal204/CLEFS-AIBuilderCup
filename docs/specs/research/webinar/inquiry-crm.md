> Research report (webinar review), gathered 2026-09-28. Items marked UNVERIFIED were not confirmed against a primary source.

## Verdict: BUILD-LITE ("Inquiry Guard"), not a full CRM

Clefs should treat unanswered or unconverted direct inquiries as money at risk. Each one runs through the existing detect → approve → execute → verify ledger, and the outcome is checked against payment webhooks and PMS reservations. Pipeline UI, marketing sequences, banquet diaries and telephony are already sold cheaply by many vendors, so they should be integrations. The part nobody else closes is the money loop: rate guardrails, verified revenue, and detecting inquiries that later came back as OTA bookings.

---

## 1. Evidence: speed-to-lead and inquiry leakage

**General speed-to-lead research (not hotel-specific)**
- HBR, "The Short Life of Online Sales Leads" (2011): an audit of 2,241 US firms. Firms that replied within 1 hour were about 7x more likely to qualify the lead, and over 60x more likely than firms that waited 24h or more. https://hbr.org/2011/03/the-short-life-of-online-sales-leads
- MIT/InsideSales Lead Response Management (2007): 6 companies, 15k+ leads. Calling at 5 minutes instead of 30 gave 100x better odds of contact and 21x better odds of qualifying. https://25649.fs1.hubspotusercontent-na2.net/hub/25649/file-13535879-pdf/docs/mit_study.pdf

**Hotel-specific evidence is thin**
- BDRC Americas mystery-shopped 70 hotels in 9 cities. No brand phoned back more than 50% of inquiries to close the business. This comes from a search snippet; the page returned 403 and the year is UNVERIFIED. https://www.costar.com/article/1142792814/how-to-handle-sales-inquiries-at-hotels
- Missed-call figures (40–60% of hotel calls unanswered, 85% of callers don't retry, about $127 per missed call) come only from voice-AI vendor blogs. Treat them as UNVERIFIED marketing claims. https://www.helloshift.com/news/how-to-stop-missing-hotel-phone-calls , https://agentzap.ai/blog/hospitality-industry-phone-statistics-15-numbers-every-hotel-owner-should-know-in-2026
- I found no credible public benchmark for direct-inquiry conversion rates. Doug Kennedy explicitly advises against industry benchmarks and says to track your own ratio (booked ÷ inquiries). https://www.hospitalitynet.org/opinion/4060836.html
- Revinate's 2026 benchmark defines "lead call conversion rate" (4.3M calls analysed), but the figures sit behind gated pages. https://www.revinate.com/hospitality-benchmark-report/
- **Implication:** Clefs should measure each hotel's own baseline in its first 30 days rather than promise a lift.

**Group / RFP response norms**
- Cvent's 2026 Global Planner Sourcing Report (1,650+ planners, 6 regions) found:
  - Planners expect an RFP response within 4 business days for small and mid-size events.
  - 24% say getting responses is their hardest sourcing problem.
  - 22% say proposals lack transparency.
  - 21% say venues answer custom questions poorly.
  - Source: https://www.cvent.com/en/blog/hospitality/inside-planner-behavior-what-data-reveals-about-group-hotel-bookings
- Three widely repeated claims come only from a vendor blog; primary source UNVERIFIED:
  - Replying within 24h makes a win 70% more likely.
  - 61% of RFPs go to one of the first three responders.
  - Cvent's Top Responder badge needs 10+ RFPs a year, a 90%+ response rate and an average reply within 2 business days.
  - Source: https://www.hippovideo.io/blog/how-to-get-cvent-top-responder-badge/

**Repeat-guest claim from the webinar ("20–40% when nurtured") is not supported for independents**
- Max Starkov says 10–15% of guests at independent hotels on a given night are repeat guests, against about 60% at chains. He cites no source. https://www.hospitalitynet.org/opinion/4125262.html
- Bookboost studied 2.2M guests at European hotel groups (2024–2026):
  - Direct bookers came back at a rate of 5.1%, against 1.3% for OTA bookers.
  - 68% of repeat stays were booked direct.
  - Source: https://insights.ehotelier.com/suppliers/2026/09/09/direct-booking-guests-return-at-4x-the-rate-of-ota-guests-what-the-data-shows/
- Treat 20–40% as UNVERIFIED. Guests-lite should measure the real share from the PMS.

## 2. Dubai: groups, corporate and long stays

**Event demand**
- GITEX GLOBAL 2026 has moved to **7–11 Dec 2026**: the Summit on 7 Dec, the Expo at Expo City Dubai on 8–11 Dec. Demand Radar calendars built on the traditional October slot are wrong. https://www.gitex.com/
- Gulfood 2027 runs 15–19 Mar 2027 at DWTC and Expo City. This comes from a search snippet of the official site; not fetched. https://www.gulfood.com/

**Bill components a quote must break out**
- 10% service charge, 7% municipality fee, and 5% VAT. The municipality fee was cut from 10% in 2018. https://gulfnews.com/news/uae/tourism/july-1-deadline-for-dubai-hotels-to-cut-municipality-fee-1.2238010
- Tourism Dirham: AED 7–20 per room-night depending on hotel class, charged for at most 30 consecutive nights. This matters for monthly-stay quotes. These are secondary sources; I could not verify DET's official schedule. https://mazeed.com/blog/hotel-tax-in-dubai/ , https://taxadvisors.ae/guides/hotel-hospitality-tax-uae/

**Hotel apartments and long stays**
- VAT: hotel apartments and serviced apartments are excluded from the "residential" exemption, so 5% VAT applies even on long stays. Secondary source; confirm with the FTA. https://bestaxca.com/vat-on-residential-property-uae/
- Published monthly rates, for reference:
  - Mercure Barsha Heights: 1BR from AED 6,500 per month. https://www.mercuredubaihotel.com/long-stay/
  - La Suite (NH Collection): studio about AED 9,845 per month. https://world.nh-hotels.com/en/nh-collection-dubai-la-suite/offers/monthly-hotel-stay-dubai
  - Rove Hotels runs a monthly-rate programme. https://www.rovehotels.com/en/offer/monthly-hotel-rates/

**What a group quote typically contains** (US and secondary sources; Dubai norms UNVERIFIED)
- Rate per night by room type, with the tax lines above shown separately.
- Offer validity date and a cutoff date, typically 30–90 days before arrival.
- Attrition: often down to about 80% of the block, sometimes stepped.
- Deposit schedule, e.g. 25% at signing, then 50% and 75% as arrival nears, or 50% up front.
- Cancellation terms and inclusions.
- Sources: https://www.canarytechnologies.com/post/hotel-attrition , https://www.mylighthouse.com/resources/blog/hotel-attrition-meaning

**UAE payment links**

| Provider | API | Pricing |
|---|---|---|
| Network International (N-Genius) | `POST /invoices/outlets/{outletRef}/invoice`, `Content-Type: application/vnd.ni-invoice.v1+json`. Fields: `invoiceExpiryDate`, `total`, `items`, `paymentAttempts`, `skipInvoiceCreatedEmailNotification`, SMS option. Payment link is returned in `_links.payment.href`. https://docs.ngenius-payments.com/reference/create-an-invoice | Bank-negotiated; UNVERIFIED |
| Telr | Unlimited payment links and invoices. https://telr.com/pricing | Small plan AED 99/month + 2.35% + AED 0.50; Medium plan AED 0/month + 2.25% + AED 0.50; +0.5% on international cards. Same page |
| Stripe UAE | Payment Links | 2.9% + AED 1; +1% international cards; +1% FX. https://stripe.com/ae/pricing |
| Tap | `POST https://api.tap.company/v2/invoices` with `due`, `expiry`, `notifications.channels` (SMS/EMAIL), and `post.url` as the webhook. https://developers.tap.company/reference/create-an-invoice | not checked |
| Checkout.com | Payment links expire after 24h by default and are one-time only. https://www.checkout.com/docs/payments/accept-payments/create-a-payment-link | not checked |

## 3. Channel APIs and constraints

**WhatsApp Cloud API** (https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- Since 1 Jul 2025, Meta bills per delivered template message.
- Non-template messages and utility templates sent inside the 24h customer-service window are free.
- UAE has its own AED rate card (1 Jul 2026), with a rate change due **1 Oct 2026**.
- Click-to-WhatsApp ads: the reply is free if the business answers within 24h, and that reply opens a **72h free window** (Android/iOS only).
- **Product signal:** a slow reply to an ad lead also costs the free window, so it is a measurable cost of slowness.

**Ad attribution on WhatsApp**
- The webhook `referral` object carries `source_url`, `source_id` (the ad ID), `source_type`, `headline`, `body`, media fields and `ctwa_clid`. https://docs.360dialog.com/docs/messaging/webhook/webhook-reference
- Send `ctwa_clid` back to Meta's Conversions API (`action_source: business_messaging`) when the booking is confirmed. https://whapi.cloud/blog/track-click-to-whatsapp-ctwa-clid

**Meta Lead Ads**
- The `leadgen` webhook gives `leadgen_id`, `page_id`, `form_id`, `ad_id`, `adgroup_id` and `created_time` only. https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/quickstart/webhooks-integration
- Fetch the answers with `GET /{lead_id}` (returns `field_data`) or `/{form_id}/leads`.
- Permissions needed: `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `ads_management`, plus App Review.
- The docs refer to a 90-day window. https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/retrieving

**Google Ads lead form webhook** (https://developers.google.com/google-ads/webhook/docs/implementation)
- The POST carries `lead_id`, `user_column_data`, `form_id`, `campaign_id`, `google_key`, `is_test`, `gcl_id`, `lead_submit_time`, `lead_source` and `lead_stage`.
- Delivery is not guaranteed exactly once, so dedupe on `lead_id`.
- Return 200 on success; a 5xx response triggers a retry.

**Instagram and Messenger** (https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)
- Standard reply window is 24h.
- The HUMAN_AGENT tag allows **manual** replies for up to 7 days; bots may not use it.
- Automated experiences must be disclosed.
- Managing other businesses' accounts needs `instagram_business_manage_messages` with Advanced Access through App Review. https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/
- **Defer to phase 2.**

**Phone**
- UAE business VoIP must route through licensed carriers (e& / du). https://ethic-it.com/sbc-ucaas-uae-gcc/
- So missed-call capture means reading call records from the hotel's licensed PBX. Specific PBX APIs are UNVERIFIED.
- Call recording needs consent (secondary source). https://frejun.com/call-recording-laws-uae/
- Telemarketing rules (Cabinet Resolution 56/2024): calls only 9am–6pm, a Do-Not-Call Registry, and fines of AED 10k–150k. https://www.clydeco.com/en/insights/2024/07/uae-tightens-telemarketing-regulations-what-you-ne
- **Do not build automated outbound calling.**
- WhatsApp's Calling API lists the UAE as supported. In practice this is UNVERIFIED because the UAE historically blocks consumer VoIP. https://developers.facebook.com/documentation/business-messaging/whatsapp/calling

## 4. Competitors

| Vendor | Inquiry/sales capability | Price | Gap vs owner profit view |
|---|---|---|---|
| Asksuite | Omnichannel inbox, "inbound reservation CRM", real-time quotes, AskFlow agents for abandonment recovery. https://asksuite.com/ | ~$150–300/month for independents (secondary; UNVERIFIED) https://hyperleap.ai/comparison/best-asksuite-alternatives-2026 | Counts conversations, not verified net revenue or OTA displacement |
| HiJiffy | Chatbot, unified inbox, quote requests for groups | €105–350/month (secondary) https://softwarefinder.com/customer-service-software/hijiffy | Same |
| Revinate Reservation Sales | Lead capture on calls, outbound follow-up, RezForce call-centre option. https://www.revinate.com/hotel-software/reservation-sales/ | Enterprise | Built for call centres and resorts |
| Tripleseat (Hotels) | Room blocks, proposals, BEOs, payment schedules, pipeline dashboard. https://tripleseat.com/products/hotels/ | ~$100–300+/month (secondary) | Events and catering focus |
| Amadeus Delphi | Separates lost / cancelled / turned-down business; lost reasons defined by the admin. https://help.amadeus-hospitality.com/sales-and-event-management/advanced/content/group-lost-business-report.html | Enterprise | Too heavy for 30–150 keys |
| Cloudbeds | Groups & Events with a Quotes tab and allotment blocks (search snippet; page 403) | Bundled | Limited to its own PMS |
| Aiosell (India) | Leads module that holds rooms/banquets, auto-expires tentatives, sends payment links by WhatsApp/email, pulls Google Ads leads. https://aiosell.com/crm-and-hotel-leads-management-system/ | UNVERIFIED | Strong fit for India; a later competitor there |
| respond.io | Inbox, lifecycle stages, workflows; webhooks on the Advanced plan | $79 / $159 / $279 per month (annual) https://respond.io/pricing | Not hotel-aware |
| Wati | WhatsApp inbox, click-to-WhatsApp ads, CRM integrations | $29 / $99 / $249 per month https://www.wati.io/pricing/ | Same |
| HubSpot Sales Hub | Starter $7 per seat (annual). Pro $90 per seat + $1,500 onboarding; forecasting and sequences start at Pro. https://www.hubspot.com/pricing/sales | | Generic |
| Zoho CRM | India: ₹800 / ₹1,400 / ₹2,400 per user per month; forecasting from Standard. https://www.zoho.com/en-us/crm/zohocrm-pricing.html. UAE about AED 55 Standard (UNVERIFIED) | | Generic |

Not checked, all UNVERIFIED: Cendyn, Knowcross, iHotelier, SiteMinder (messaging only, per Hotel Tech Report), eZee, Hotelogix, STAAH, Pipedrive (pricing page 403), Interakt.

**What is commoditized:** inboxes, pipeline stages, chatbots, payment links.

**Gap nobody closes:**
- Pricing a quote against comp rates and event demand.
- Proving the booking happened.
- Costing an inquiry that leaked to an OTA.
- An owner-level approval ledger.

## 5. Lost reasons and forecasting

**Terminal states** (the Delphi distinction): LOST (guest walked), TURNED_DOWN (hotel declined), CANCELLED (after confirmation).

**Lost-reason codes, v1**
- PRICE_VS_COMP: store a snapshot of competitor rates from Demand Radar.
- NO_AVAILABILITY / DISPLACEMENT
- SLOW_RESPONSE: set automatically when the SLA was breached.
- BOOKED_VIA_OTA: set automatically when an OTA reservation matches the inquiry's name, phone or dates; it carries the commission cost into OTA Profit Guard.
- BOOKED_COMPETITOR
- GUEST_UNRESPONSIVE
- PAYMENT_LINK_EXPIRED
- POLICY_TERMS (deposit, cancellation, attrition)
- DATES_CHANGED → move to FUTURE_TRAVEL with a reminder date
- PRODUCT_LOCATION_MISMATCH
- TRIP_CANCELLED
- SPAM_DUPLICATE

**Forecast**
- Weighted pipeline = Σ (room-nights × expected ADR × stage probability).
- Starting probabilities are assumptions: New 10%, Quoted 30%, Negotiation 50%, Payment pending 80%.
- Recalibrate them per hotel after about 90 days of verified outcomes.

## 6. Recommended Inquiry Guard design

**Stages:** only New → Replied → Quoted → Payment pending → Confirmed (verified) / Lost (reason) / Future.

**Intake**
- v1:
  - WhatsApp Cloud API (reuses the concierge number), keeping the ad referral data and `ctwa_clid`.
  - An email forwarding address, or Gmail / Microsoft Graph.
  - Website form and chat widget.
  - Meta `leadgen` and Google lead-form webhooks.
  - A "log a walk-in or phone inquiry" WhatsApp command for staff.
- v2: Instagram DMs (after App Review) and PBX call records for missed calls.
- An LLM extracts dates, number of guests and rooms, segment (FIT / corporate / group / long-stay / event), budget and language (Arabic/English); inquiries are deduped by phone and email.

**Signals**
- First-response SLA per segment: FIT ≤15 min in business hours, group ≤4h, RFP ≤24h (starting defaults).
- Money at risk = est. room-nights × Demand Radar expected ADR × stage probability.
- Flag ad leads close to losing the 24h free-reply window.

**Quote draft**
- Price = best available rate, clamped between a floor and an event-driven uplift from Demand Radar.
- Dubai tax lines shown separately, with the Tourism Dirham capped at 30 nights for long stays.
- Offer validity (shorter during high-demand periods), deposit and cancellation terms.
- For groups: cutoff date and attrition.
- Payment link generated through the hotel's own gateway (N-Genius, Telr, Stripe, Tap or Checkout.com) behind one adapter.

**Approval**
- One tap in the owner's daily WhatsApp brief.
- Automatic send for FIT quotes inside the rate guardrails.

**Verification (the key feature)**
- Payment webhook plus a matching PMS reservation within N days = "Verified direct revenue", logged with the commission saved versus the OTA.
- If the same guest later appears through an OTA = "Leaked", logged with the commission cost.

**Owner brief example:** "9 inquiries (AED 21k potential); 2 unanswered >2h (AED 5.4k at risk); 1 group quote awaiting approval; last week 4 verified bookings, AED 11.2k, AED 1.7k commission avoided."

**Ask Clefs metrics:** response time p50/p90 by channel, inquiry→verified-booking conversion, lost-reason mix, share of inquiries leaking to OTAs.

**Leave to integrations**
- Banquet and wedding diaries, BEOs, function space: Tripleseat, Delphi, Aiosell or the PMS.
- Telephony: a licensed PBX.
- E-signature.
- Nurture sequences and a large shared-inbox product: respond.io or Wati, or a thin Clefs inbox.

**Reject**
- A full CRM UI.
- Lead scoring.
- Automated outbound calling (TDRA rules).
- Any promise of repeat share or conversion lift before the hotel has its own baseline.

**Defensible vs commoditized**
- Defensible: rate guardrails, PMS/payment verification, OTA-leak attribution, the actions ledger, and Arabic plus Dubai-tax-aware quotes.
- Commoditized: inboxes, pipeline stages, chatbots, payment links.

---
