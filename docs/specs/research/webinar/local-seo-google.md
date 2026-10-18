> Research report (webinar review), gathered 2026-09-28. Items marked UNVERIFIED were not confirmed against a primary source.

## Verdict: build a lite version, framed as "Visibility → direct revenue" and not as a local SEO tool

- **Rank grids and GBP dashboards are commoditized.** Local Falcon starts at $24.99/mo for 7,500 grid-point credits ([pricing](https://www.localfalcon.com/pricing)). BrightLocal Track is about $39/mo per location according to third-party sources ([stackscored](https://www.stackscored.com/pricing/local-seo-tools/brightlocal/)); BrightLocal's own page now shows "price on request" ([brightlocal](https://www.brightlocal.com/pricing/)). Local Viking is reportedly from $39/mo ([gbppromote](https://gbppromote.com/local-viking-review/)). Clefs should not sell heatmaps.
- **What is defensible is joining Google's surfaces to Clefs' money data.** Four things only Clefs can connect:
  - Does the hotel's official site show a price on Google Hotels?
  - Is that price at parity with the OTAs sitting next to it?
  - How many direct bookings came from Google (UTM links matched to PMS/booking engine data)?
  - How much OTA commission did that save? This feeds OTA Profit Guard.
- **Marginal cost is about $2–5 per hotel per month**, because the rank grid can run for $0 on Google's official Places API IDs-only search (details in section 6).

---

## 1. Local rank tracking

**How the grid tools work**
- They lay a grid of points (3×3 up to 21×21) around the hotel and run the same Maps query at each point. One point costs one credit, so a 5×5 scan is 25 credits ([Local Falcon pricing](https://www.localfalcon.com/pricing)).
- They record rank up to position 20 and report three metrics ([Local Falcon llms.txt](https://www.localfalcon.com/llms.txt)):
  - **ARP:** average rank over points where the hotel was found.
  - **ATRP:** average over all points, counting "not found" as 20+.
  - **SoLV:** percentage of points where the hotel is in the top 3.

**Option A: SerpApi `google_maps` engine** ([docs](https://serpapi.com/google-maps-api))
- Request: `engine=google_maps&type=search&q=...&ll=@lat,lng,14z`. Alternatives are `lat`/`lon` with `z` (3–30) or `m`, plus `hl`/`gl`. Paginate with `start` in steps of 20.
- Each `local_results[]` item has `position`, `place_id`, `data_id`, `data_cid`, `gps_coordinates`, `rating`, `reviews`, `price`, `type`.
- The docs do not say whether ads or sponsored results are included (UNVERIFIED).
- The `google_local` engine takes `location`/`uule` and returns similar fields ([docs](https://serpapi.com/google-local-api)).
- **Pricing** ([serpapi.com/pricing](https://serpapi.com/pricing)):
  - Production: $150 for 15k searches (about $0.01 each).
  - Big Data: $275 for 30k.
  - Hourly cap is about 20% of the monthly volume.
  - Cached and failed searches are not billed.

**Option B (recommended primary): Places API (New) Text Search, IDs only**
- Request: `POST places.googleapis.com/v1/places:searchText` with a `locationBias` circle (radius up to 50 km), `rankPreference=RELEVANCE`, and `includedType=lodging` or `strictTypeFiltering`. Returns 20 results per page, 60 maximum ([docs](https://developers.google.com/maps/documentation/places/web-service/text-search)).
- With field mask `places.id`, the "Text Search Essentials (IDs Only)" SKU has an **unlimited free cap**. Pro is $32 per 1k and Enterprise is $35 per 1k ([pricing](https://developers.google.com/maps/billing-and-pricing/pricing)).
- Clefs already knows the hotel's and competitors' place_ids, so an ordered list of IDs is enough to compute rank.
- **Caveat:** Google does not say this ordering matches the Maps app ranking (UNVERIFIED). Calibrate it monthly against a small SerpApi sample.

**Terms-of-service risk**
- Google sued SerpApi on 2025-12-19 under DMCA §1201 ([Google blog](https://blog.google/technology/safety-security/serpapi-lawsuit/)).
- The court granted SerpApi's motion to dismiss on 2026-07-20 ([SerpApi](https://serpapi.com/blog/google-v-serpapi-the-court-granted-our-motion-to-dismiss/)).
- A narrower re-filing is reported but UNVERIFIED.
- Using the official Places API removes this risk for the core metric.

**How hotel queries behave**
- For "hotels in X", Google shows a hotel pack with prices and date pickers instead of the usual website and directions links ([Search Engine Land](https://searchengineland.com/guide/google-local-pack)). So the Maps pack rank is only half the picture.
- **Google Hotels via SerpApi `google_hotels`:**
  - Requires `q`, `check_in_date` and `check_out_date`. Accepts `gl`, `hl`, `currency`, `adults`, `sort_by`, `hotel_class` and `next_page_token` ([docs](https://serpapi.com/google-hotels-api)).
  - `properties[]` carries `sponsored` (bool), `type`, `property_token`, `overall_rating`, `reviews`, `extracted_hotel_class` and `rate_per_night` ([docs](https://serpapi.com/google-hotels-properties)). Sponsored listings also appear in a separate `ads[]`.
  - **Organic position** = the hotel's index in `properties[]` after dropping `sponsored=true` items, recorded for fixed date pairs (e.g., next Friday and a date 30 days out).
  - Property details `prices[]` and `featured_prices[]` include **`official: true`** for the hotel's own site ([docs](https://serpapi.com/google-hotels-property-details)). This is how Clefs detects free-booking-link presence and checks parity against OTAs.
- Google Hotels shows a sponsored row first, then organic results ([scalemyhotel](https://scalemyhotel.com/blog/google-hotel-ads/free-booking-links-vs-paid-hotel-ads/), secondary source).

## 2. Google Business Profile APIs (as of 2026)

**Access**
- Requirements: a GBP verified and active for 60+ days, plus a website. Apply through the API contact form; 0 QPM means not approved and 300 QPM means approved ([prereqs](https://developers.google.com/my-business/content/prereqs)).
- OAuth scope `business.manage`.

**Performance API** ([reference](https://developers.google.com/my-business/reference/performance/rest))
- Daily metrics: `GET /v1/locations/*:fetchMultiDailyMetricsTimeSeries` (also `getDailyMetricsTimeSeries`).
- `DailyMetric` values ([enum](https://developers.google.com/my-business/reference/performance/rest/v1/DailyMetric)):
  - Impressions: `BUSINESS_IMPRESSIONS_{DESKTOP,MOBILE}_{MAPS,SEARCH}`, counted as unique users per day.
  - Actions: `BUSINESS_CONVERSATIONS`, `BUSINESS_DIRECTION_REQUESTS`, `CALL_CLICKS`, `WEBSITE_CLICKS`.
  - `BUSINESS_BOOKINGS` counts **Reserve with Google** bookings only, so it does not capture hotel bookings.
  - `BUSINESS_FOOD_ORDERS` is deprecated.
- Search keywords: `GET /v1/locations/*/searchkeywords/impressions/monthly` with `monthlyRange` and `pageSize` up to 100 ([ref](https://developers.google.com/my-business/reference/performance/rest/v1/locations.searchkeywords.impressions.monthly/list)).
  - `insightsValue` is either `value` or `threshold`, where threshold means a low-volume keyword. Never add thresholds together.
- Data lag and history window are not documented (UNVERIFIED).

**Reviews (v4)**
- `accounts.locations.reviews.list` and `PUT .../reviews/*/reply` are active ([ref](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.reviews/updateReply)).
- Added in 2026 ([latest updates](https://developers.google.com/my-business/content/latest-updates)):
  - `ReviewReplyState` moderation status (2026-04-01).
  - Review media (2026-04-20).
  - `PolicyViolation` (2026-07-01).
  - `reviewReplyUrl` (2026-07-24).
  - Posts gained `RecurrenceInfo` for recurring posts (2026-04-07).
- Real-time alerts via Pub/Sub: `NEW_REVIEW` and `UPDATED_REVIEW` through Notifications v1 `updateNotificationSetting` ([ref](https://developers.google.com/my-business/reference/notifications/rest/v1/NotificationSetting)).

**Q&A**
- The API was discontinued on 2025-11-03 ([changelog](https://developers.google.com/my-business/content/qanda/change-log)). Its role is being taken over by Gemini "Ask Maps" answers drawn from profile, reviews and website ([Yext](https://www.yext.com/blog/2025/10/google-retiring-q-and-a-ai-driven-answers)).

**Lodging API**
- `getLodging`, `updateLodging`, `getGoogleUpdated` ([ref](https://developers.google.com/my-business/reference/lodging/rest/v1/Lodging)).
- Field groups: property, services, policies (`checkinTime` and `checkoutTime` as TimeOfDay), foodAndDrink, pools, wellness, accessibility, connectivity, parking, pets, sustainability, guestUnits.
- Google says hotel attributes **cannot be updated by spreadsheet**, only through the API or dashboard ([help](https://support.google.com/business/answer/9177814?hl=en)).

**Hotel restrictions**
- Hotels can publish Update and Event posts but **no Offer or promotional posts**.
- No booking URL or booking button, no products, no description.
- Hotel class comes from the official classification body where Google has access, otherwise from OTAs.
- Source for all of the above: [BrightLocal](https://www.brightlocal.com/learn/google-business-profile-hotels/) (secondary; Google does not confirm on one page).

## 3. Google Hotel Center and free booking links

**The Sep 30 sunset**
- Verified: "Google third-party rates for hotel ads… will be unavailable after September 30, 2026." It affects **Hotel Ads and Performance Max for Travel Goals** campaigns without a Hotel Center feed ([Google help](https://support.google.com/hotelprices/answer/14310302)).
- It is **not** a free-booking-link change ([Triptease](https://www.triptease.com/resources/google-is-ending-third-party-rates)).
- Hotels relying on it must set up a direct feed through a partner or a Hotel Center account and rebuild their campaigns.

**Related changes**
- Self-serve hotel rates entered through GBP ended on 2025-07-30 ([HC announcements](https://support.google.com/hotelprices/announcements/9652284?hl=en)). The only route to an "official site" price is now a Hotel Center feed.
- Price-accuracy enforcement was tightened for **ads and free booking links** in an announcement dated 2025-09-29 ([help](https://support.google.com/hotelprices/answer/16597854?hl=en)). Price accuracy policy: [6064419](https://support.google.com/hotelprices/answer/6064419?hl=en).

**Free booking links**
- Free per click. Two ways in: a connectivity partner or a direct Hotel Center account.
- Ranking inputs include user preference, value, landing-page experience and historical price accuracy ([help](https://support.google.com/hotelprices/answer/10472393?hl=en)).
- Bids do not affect free-link rank ([Cloudbeds](https://www.cloudbeds.com/articles/googles-free-hotel-booking-links/)).

**Reporting**
- Hotel Center shows impressions, clicks and CTR by property, country, device and more. Free-link impressions are rounded, data takes 2–3 days, and CSV exports cap at 10k rows ([help](https://support.google.com/hotelprices/answer/10474165?hl=en)).
- API: `GET travelpartner.googleapis.com/v3/accounts/*/freeBookingLinksReportViews:query`. It is deprecated in favour of `PropertyPerformanceReportService` ([ref](https://developers.google.com/hotels/hotel-prices/api-reference/rest/v3/accounts.freeBookingLinksReportViews/query)).
- The Hotel Center account is usually owned by the partner, so hotel-level access is UNVERIFIED.

**Connectivity partners** ([Google list](https://developers.google.com/hotels/connectivity-partners), ads-capable unless noted)
- **Listed:** eZee, STAAH, SiteMinder, Cloudbeds, Asiatech (ResAvenue), Djubo, Simplotel, Stayflexi, D-EDGE, SynXis, Amadeus, HotelRunner, Exely, Profitroom, Mirai, Triptease, Bookassist.
- **Listed but not ads-capable:** AxisRooms, Bookingjini, HyperGuest.
- **Not found:** Mews, Opera, Hotelogix, IDS Next, RateGain.
- The list has no country filter, so which partners dominate in the UAE is UNVERIFIED.

## 4. Website essentials

- **Google's own Hotel price structured data** (`Hotel` + `HotelRoom` JSON-LD) is used for **price-accuracy validation**. Required: `name`, `address`, `makesOffer`/`offers`. Recommended: room `bed` and `occupancy` ([Google](https://developers.google.com/hotels/hotel-prices/structured-data/hotel-price-structured-data)).
- Google's LocalBusiness rich-result docs do not cover Hotel ([docs](https://developers.google.com/search/docs/appearance/structured-data/local-business)).
- Other useful schema.org fields: `checkinTime`, `checkoutTime`, `starRating`, `numberOfRooms`, `amenityFeature`, `petsAllowed`, `priceRange`, `geo` ([schema.org](https://schema.org/Hotel)). They are also fodder for the AI answers that replaced Q&A.
- **Core Web Vitals "good" thresholds** at the 75th percentile: LCP ≤2.5s, INP ≤200ms, CLS ≤0.1 ([web.dev](https://web.dev/articles/vitals)).
  - The CrUX API is free at 150 queries/min ([Chrome](https://developer.chrome.com/docs/crux/api)).
  - PageSpeed Insights API: about 25k/day ([SEJ](https://www.searchenginejournal.com/extracting-cwv-crux-seo/430020/), secondary).
- The landing page must show the same price and taxes as Google, which is part of the price-accuracy policy ([help](https://support.google.com/hotelprices/answer/6064419?hl=en)).
- Booking-engine placement and rate-parity messaging: best practice only, no primary source (UNVERIFIED).

## 5. KPIs Clefs should compute

| KPI | Source | Formula |
|---|---|---|
| Profile reach | Performance API | Sum of 4 impression metrics, last 28 days vs prior 28 days and vs last year |
| Action rate | Performance API | (CALL + WEBSITE + DIRECTIONS + CONVERSATIONS) / impressions |
| Branded vs discovery searches | searchkeywords | Share of keyword impressions not containing the hotel name (exact values only) |
| Map SoTP3 / ATRP | Places IDs-only grid | 7×7 grid × 3–5 keywords, weekly |
| Google Hotels organic rank | `google_hotels` | Index after removing sponsored results, for 2 date pairs × 2–3 area queries |
| Official-rate presence and parity | property details | `official` present? official price − min(OTA price) |
| Review velocity vs comp set | GBP reviews + SerpApi/Places counts | New reviews per 30 days vs comp median; rating trend |
| Response rate and time | reviews.list | % with reply; median reply time |
| Free-booking-link clicks | Hotel Center via partner | Clicks and CTR per week (only if data is shared) |
| Site health | CrUX + JSON-LD fetch | p75 LCP/INP/CLS on mobile; required schema fields present |

## 6. Recommended Clefs features

**What goes in the owner brief (one line each):**
- "Official rate missing on Google Hotels" (no `official` price found).
- "Your direct rate is AED X above Booking.com on Google" (parity check, linked to OTA Profit Guard).
- "Top-3 share on 'hotels near Dubai Mall' fell from 44% to 28%. Comp Y gained 19 reviews."
- Monthly "Google sent N website clicks → M direct bookings → AED Z commission avoided". This needs a UTM on the GBP `websiteUri` matched to booking-engine/PMS data.

**Actions the owner can approve, fitting detect → approve → execute → verify:**
1. **Review reply:** `updateReply`, then verify with `ReviewReplyState`.
2. **Tag the GBP website link with UTM:** Business Information `locations.patch websiteUri`.
3. **Fix lodging facts vs competitors:** `updateLodging` (amenities, check-in time), then verify with `getGoogleUpdated`.
4. **Recurring non-promotional Update post:** `localPosts` with `RecurrenceInfo`. Guardrails block offer or discount language.
5. **Parity fix:** a task for the channel manager or revenue manager; verified by re-running property details.
6. **Sunset / free-booking-link enablement:** if a hotel runs PMax Travel Goals without Hotel Center or shows no official price, send a WhatsApp checklist naming their partner from Google's list.
7. **Review-request push to recent guests** via Guests-lite.

**Marketing lead magnet:** add a free "Are you on Google's free booking links, and at parity?" check next to the OTA-statement audit.

**Cost per hotel per month (estimate)**

| Item | Cost |
|---|---|
| Places IDs-only grid (~1,000 calls) | $0 |
| SerpApi calibration (~100 searches at about $0.01) | ~$1 |
| `google_hotels` + details (~150 searches, shared with Demand Radar) | ~$1.5 |
| GBP, CrUX and PSI APIs | $0 |
| LLM summaries | <$1 |
| **Total** | **~$2–5** |

**Commoditized:** geo-grid heatmaps, GBP post scheduling, review monitoring (BrightLocal, Local Falcon, Birdeye).

**Defensible:**
- Official-rate presence plus parity on Google Hotels, tied to OTA commission figures.
- Direct-booking attribution from Google.
- Approval-gated execution with verification.
- Arabic replies and UAE comp sets.
- Pairing ranking changes with review velocity and service-quality data from Service Control.

**Reject:** a standalone rank-tracking UI, keyword-stuffing GBP advice, and Q&A seeding (the API is gone).

---
Note: the "claude.ai Fortes M365" connector needs to be authorized in the user's claude.ai connector settings before it can be used; it was not needed for this research.
