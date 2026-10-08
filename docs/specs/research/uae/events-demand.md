> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

# Demand Radar research: data sources and demand drivers for Dubai/UAE hotels (as of 2026-09-27)

## 0. What changed in 2026

- **The US–Iran conflict hit Dubai hotels hard.** Hostilities began in early March 2026. A two-week ceasefire was announced on 8 April 2026. Dubai occupancy averaged 84.8% in Jan–Feb, then fell to 22.8% in the week ending 14 March. Cancellations reached 60% within 48 hours. https://www.hvs.com/article/10449-shock-divergence-and-recovery-the-impact-of-the-2026-usiran-conflict-on-gcc-hospitality
- **Recovery is under way but not complete.** DET reports occupancy of 36% in March and 66% in August 2026, which is 89% of August 2025. Dubai recorded 21.61M room nights in Jan–Aug. Supply was about 149,000 rooms at the end of August. https://www.bwtravel.com/industry-insights/dubai-tourism-records-sustained-growth-through-august-2026-12531735
- **RevPAR is down 31% year to date through June.** Hotels are discounting and selling UAE/GCC staycation bundles. There are more visitors from Asia and fewer from Europe. https://www.thenationalnews.com/news/uae/2026/09/14/uaes-hospitality-sector-shifts-from-crisis-to-recovery-amid-iran-war-fallout/
- **Many events were moved.** Several exhibitions originally set for March–June were rescheduled. ATM moved from 4–7 May to 17–20 Aug and then to 14–17 Sep 2026. https://gulfbusiness.com/en/2026/tourism/arabian-travel-market-september-2026-dubai/ and https://www.thenationalnews.com/news/uae/2026/09/09/dubai-events-calendar-gitex-arabian-travel-market/
- **Implication for Clefs:** 2026 history is badly distorted. Seasonality baselines should use 2024–2025. Each event record needs `status` (scheduled / rescheduled / cancelled) and `original_dates` fields.

---

## 1. Event data sources

| Source | How you get the data | Notes |
|---|---|---|
| **PredictHQ Events API** | `GET https://api.predicthq.com/v1/events/` with header `Authorization: Bearer <key>` | Location filters: `country=AE`, `place.scope`, `within={radius}{unit}@{lat},{lon}`. Other filters: `category`, `rank.gte`, `local_rank.gte`, `phq_attendance.gte`, `predicted_event_spend.gte`, `active.gte/lte`, `active.tz`, `q`, `sort`, `limit` (default 10), `offset`. Response fields: `id, title, category, state, start, end, start_local, end_local, predicted_end, timezone, duration, geo, place_hierarchies, entities, rank, local_rank, phq_attendance, predicted_event_spend, predicted_event_spend_industries (accommodation/hospitality/transportation), impact_patterns, phq_labels, labels, brand_safe, updated, first_seen`. Categories include `conferences, expos, concerts, festivals, sports, performing-arts, community, public-holidays, school-holidays, observances, severe-weather`. https://docs.predicthq.com/api/events/search-events |
| **PredictHQ Features API** (the best fit for a demand radar) | `POST https://api.predicthq.com/v1/features/` | Body: `active.gte/lte` plus `location.geo {lat, lon, radius}`, `place_id` (max 3) or `saved_location_id`. Features include `phq_attendance_conferences`, `_expos`, `_concerts`, `_sports`, `_festivals`, `_community`, `_performing_arts`, `_school_holidays`, the `*_hospitality` impact-pattern variants, and `phq_rank_public_holidays`. Stats: `count, sum, min, max, avg, median, std_dev`. It returns one row per day. https://docs.predicthq.com/api/features/get-features |
| PredictHQ limits and pricing | | Rate limits are counted in requests per second across the whole organisation, and you get a 429 when you go over. https://docs.predicthq.com/api/overview/rate-limits.md. There is a 14-day trial with no card required; trial exports are capped below 1,000 records. https://www.predicthq.com/support/learn-about-our-trial. After the trial, PredictHQ's own pages disagree: one says you drop to a Free plan, the other says access is blocked. https://www.predicthq.com/support/what-happens-at-the-end-of-my-free-trial. Third-party listings say "from $75/month" with location-based pricing (GetApp/G2). **Actual price: UNVERIFIED**, because the official pricing page returned HTTP 405. **How deep UAE coverage goes: UNVERIFIED**, so test Dubai recall during the trial. |
| **Ticketmaster** | Discovery API: `https://app.ticketmaster.com/discovery/v2/events.json` with `countryCode`, `geoPoint`, `radius`, `startDateTime`, `endDateTime`, `classificationName`, `size`, `page`. Discovery Feed 2.0: `https://app.ticketmaster.com/discovery-feed/v2/events.json?apikey=…&countryCode=AE` | Discovery API: 5,000 calls/day, 5 requests/second, and paging must stay under size×page < 1000. https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/. The Feed 2.0 docs list AE explicitly and offer JSON or CSV. https://developer.ticketmaster.com/products-and-docs/apis/discovery-feed/. **How many UAE events it actually covers: UNVERIFIED.** Most UAE ticketing runs through Platinumlist or venue-owned sites, so expect thin coverage. |
| **Platinumlist** | No documented public developer API | Partners get XML/API access by agreement. https://platinumlist.net/for-organisers/ticketing-features/. Affiliates earn up to 10% commission (Tapfiliate). https://platinumlist.net/for-organisers/affiliate-program/. Venue pages exist, e.g. https://dubai.platinumlist.net/venue/6351/coca-cola-arena. Treat this as a partnership conversation, not something to scrape. |
| **Eventbrite** | Search is gone | `GET /v3/events/search/` was removed on 12 Dec 2019. Only lookups by venue or organisation ID remain. https://www.eventbrite.com/platform/docs/by-location |
| **DWTC** | https://www.dwtc.com/en/events/ | Month view with Event Type and Industry filters. **No ICS, RSS or JSON feed was found.** The best structured list is the calendar DWTC publishes for each season. https://exhibitcitynews.com/dwtc-2026-events-calendar/ |
| **Dubai Exhibition Centre (Expo City)** | https://www.dubaiexhibitioncentre.com/en/whats-on | Card list with a "Load More" button, showing title, dates, type and hall. No feed found. |
| **Visit Dubai / Dubai Calendar** | https://www.visitdubai.com/en/festivals-and-events/dubai-events-calendar | Filters for type, date, category and free events. No export found. |
| **ADNEC** | Press releases | More than 190 events in H1 2026 and more than 290 in H2 2026. https://www.mediaoffice.abudhabi/en/tourism/adnec-group-to-host-290-events-across-abu-dhabi-and-al-ain-in-h2-2026/ |
| **Expo Centre Sharjah** | https://expo-centre.ae/2026/ | A yearly list of more than 95 exhibitions. https://expo-centre.ae/expo-centre-sharjah-sets-ambitious-2026-events-calendar-featuring-95-exhibitions/ |
| **Arenas** | Venue sites | Coca-Cola Arena (City Walk) holds 17,000: https://en.wikipedia.org/wiki/Coca-Cola_Arena. Etihad Arena (Yas) holds 18,000: https://en.wikipedia.org/wiki/Etihad_Arena. No feeds found. |

**Recommended ingestion approach:**
1. Maintain a curated `venue_events` table, seeded each quarter from the DWTC, DEC, ADNEC and Expo Centre Sharjah calendars. Store `source_url` and `last_verified_at`.
2. Use the PredictHQ Features API as the programmatic backbone.
3. Add the Ticketmaster AE feed for concerts.
4. Keep a holiday table in which Hijri dates carry a `moon_dependent=true` flag and a ±1-day window.

---

## 2. Demand drivers: Q4 2026 to 2027

Venue codes: **DWTC** = Trade Centre / Sheikh Zayed Road corridor. **DEC** = Expo City / Dubai South.

| Dates | Event | Venue / notes | Source |
|---|---|---|---|
| Oct 5–7 2026 | CABSAT / SATExpo | DEC | dubaiexhibitioncentre.com/en/whats-on |
| Oct 6–8 | Beautyworld | DWTC | exhibitcitynews (above) |
| Oct 12–14 | Airport Show | DWTC | same |
| Oct 12–16 | UAE school mid-term break | | https://gulfnews.com/living-in-uae/education/uae-school-holidays-2026-27-key-dates-1.500637553 |
| Oct 31–Nov 29 | Dubai Fitness Challenge | | https://gulfnews.com/uae/dubai-fitness-challenge-2026-dates-announced-1.500639620 |
| Nov 2–5 | ADIPEC | ADNEC | https://www.adipec.com/ |
| Nov 3–5 | Gulfood Manufacturing + Gulf Host | DWTC | exhibitcitynews |
| Nov 8 | Diwali | | https://publicholidays.com/diwali/ |
| Nov 10–12 | Automechanika | DEC | exhibitcitynews |
| Nov 20–28 | Saudi autumn school break | | https://whichschooladvisor.com/saudi/school-news/new-saudi-school-calendar-holidays-for-2026-2027 |
| Nov 23–26 | Big 5 Global | DWTC | https://www.big5global.com/ |
| Nov 27–29 | Emirates Dubai 7s | Sevens Stadium; more than 88,000 attended in 2025 | https://www.thenationalnews.com/sport/2026/08/31/dubai-sevens-2026-tickets-now-on-sale-as-gaelic-football-is-added-to-programme/ |
| Dec 2–3 (Wed–Thu) | Eid Al Etihad (National Day) | | https://www.timeoutdubai.com/news/eid-al-etihad-2026-day-off-residents |
| Dec 3–6 | F1 Abu Dhabi GP | Yas Marina | https://gulfnews.com/sport/motorsport/formula-1-confirms-abu-dhabi-grand-prix-will-close-out-2026-season-in-december-1.500649679 |
| Dec 4–8 / Dec 7–11 | GITEX Global Summit (DWTC) / **GITEX Global (DEC)** | More than 200,000 attendees and 6,800 exhibitors | https://www.gitex.com/ |
| Dec 10–12 | MEBAA | DWTC | exhibitcitynews |
| ~Dec 13 – Jan 19 | Dubai Shopping Festival | **UNVERIFIED** (not officially announced yet) | https://www.dubaidet.gov.ae/en/newsroom/press-releases/31st-edition-of-dubai-shopping-festival |
| Dec 14 – Jan 3 | UAE school winter break | | Gulf News (above) |
| Dec 25 / Dec 31 | Christmas / New Year's Eve | NYE 2025: 94.1% occupancy, ADR AED 2,286.60 | https://www.costar.com/products/str-benchmark/resources/press-releases/dubai-hotel-industry-posts-best-december |
| From Jan 8 2027 | Saudi mid-year break | | whichschooladvisor (above) |
| Jan 25–28 2027 | World Health Expo (WHX) | DEC + DWTC | https://www.worldhealthexpo.com/events/healthcare/dubai/ |
| Jan 25–29 | IDEX | ADNEC | https://www.idexuae.ae/en |
| Feb 6 | Chinese New Year | | https://en.wikipedia.org/wiki/Chinese_New_Year |
| ~Feb 8 – ~Mar 9 | Ramadan (projected) | | https://www.thenationalnews.com/news/uae/2026/09/09/ramadan-2027-dates-eid-al-fitr-holiday/ |
| ~Mar 9/10–11/12 | Eid Al Fitr | Moon-dependent | https://gulfnews.com/living-in-uae/ask-us/uae-public-holidays-2027-1.500638729 |
| Mar 15–19 | Gulfood | DWTC + DEC; more than 8,500 exhibitors | https://www.gulfood.com/ |
| Apr 5–11 | UAE school spring break | | Gulf News |
| May 15 / May 16–18 | Arafat Day / Eid Al Adha (projected) | | Gulf News 2027 list |
| ~Jun 6 / ~Aug 14 | Islamic New Year / Prophet's Birthday | | same |
| Jul 2 | UAE school year ends | | Gulf News |
| Nov 15–19 2027 | Dubai Airshow | DWC; more than 85,000 trade visitors | https://www.dubaiairshow.aero/en/dubai-airshow.html |
| Dec 2–3 2027 | Eid Al Etihad | | Gulf News |

**2026 actuals, for back-testing:**
- Eid Al Fitr: 20 Mar
- Arafat Day: 26 May; Eid Al Adha: 27–29 May
- Islamic New Year: moved to 15 Jun (holiday-transfer rule; Muharram actually began 16 Jun). https://www.khaleejtimes.com/uae/muharram-2026-islamic-new-year-1448-ah-june-16-start
- Prophet's Birthday: 25 Aug

Holiday entitlements come from Cabinet Resolution No. 27 of 2024 (see the Arabian Business 2027 article).

**UNVERIFIED (not published or not found):**
- 2027 dates for GITEX, ATM, Big 5 and the Dubai Fitness Challenge.
- Term dates for Indian-curriculum schools in the UAE. Gulf News confirms they differ from the unified calendar but gives no dates.
- School holidays in India.

---

## 3. How Dubai hotel demand behaves

- **Weekend.** Since 1 Jan 2022 the UAE week is 4.5 days: Friday afternoon, Saturday and Sunday are off. https://gulfnews.com/uae/saturday-sunday-weekend-from-january-1-in-uae-four-and-half-day-working-week-1.1638864606070
  - Suggested defaults: residents' staycation nights are Fri and Sat. Corporate and MICE demand runs Mon–Thu nights. A holiday that falls on a Wed or Thu creates a bridge. For example, 2–3 Dec 2026 plus Friday 4 Dec as leave gives five days. https://gulfnews.com/living-in-uae/ask-us/uae-national-day-eid-al-etihad-2026-holiday-1.500645564
- **Seasonality.** Peak runs Nov–Mar and summer is the low.
  - 2025: occupancy 80.7%, ADR AED 579, 44.85M room nights. https://mediaoffice.ae/en/news/2026/february/09-02/dubais-tourism-industry-achieves-third-successive-record-breaking-year
  - December 2025 was the best December since 2006–07.
  - August 2025 occupancy works out to about 74%. That figure is derived: 66 ÷ 0.89.
  - Ramadan softens leisure demand; Eid brings a GCC surge. This is industry pattern and is **UNVERIFIED** here.
- **Source markets, Jan–Aug 2026 (DET):** Western Europe 20%, South Asia 17%, GCC 16%, CIS/Eastern Europe 14%, MENA 10%, NE/SE Asia 9%, Americas 7%, Africa 5%, Australasia 2%. That is 6.97M visitors. See the bwtravel link above.
  - Map each market's calendar into the radar: school holidays in the GCC and Europe, Diwali for South Asia, Chinese New Year for NE Asia.
- **Where events create compression:**
  - **DWTC events** (Big 5, Beautyworld, ATM, MEBAA) push demand along Sheikh Zayed Road and the Metro Red Line: Trade Centre, Karama, Bur Dubai, Deira, Al Barsha. Deira and Bur Dubai 3–4★ hotels have gone from 60% to 80% occupancy on show pickup. https://gulfnews.com/business/tourism/dubais-hotels-await-quick-return-to-80-90-occupancy-as-trade-shows-exhibitions-open-1.1674186680577
  - **GITEX has moved to DEC**, so compression shifts to Expo City and Dubai South. Rove Expo City and Dubai South hotels are reported to sell out first, with rates 25–40% higher than an October GITEX. Those sources are not primary, so the rate uplift is **UNVERIFIED**. https://mayratours.com/gitex-2026-expo-city-business-traveller-guide/
  - **Dual-venue shows** (Gulfood 2027, WHX 2027) compress both corridors.
  - **Model area impact as distance decay from each venue**, tagged by `corridor` (DWTC / DEC / Yas / Sharjah).
  - **No area-level occupancy data was found (UNVERIFIED).** Calibrate with each client's own PMS pickup data.

---

## 4. Rate and competitor data without scraping OTAs yourself

| Option | Access and specs | Cost | Legal / practical |
|---|---|---|---|
| **Lighthouse Integration API** | `https://api.mylighthouse.com/v3`, header `X-Oi-Authorization`. Endpoints: Hotels, Markets, Rates, Demand, Ranking, Reviews, Parity, Market Insight (351-day outlook). Limits: 20 requests per API per subscription per 24h, and 120 per minute globally. JSON or CSV. | Not public; free trial on independent-hotel plans | Cleanest option. Requires the hotel to subscribe, or Clefs to become a certified partner. These details come from a third-party profile: https://github.com/api-evangelist/lighthouse-travel |
| **RateGain Navigator** | REST integration | About $150–800/month (third-party estimate) | Partner route. https://hoteltechreport.com/revenue-management/market-intelligence-tools/rateshopper-by-rategain |
| **MakCorps** | Prices from 200+ OTAs; claims history back to 2010 | Free: 30 calls. Basic: $350/month for 10k. Advance: $500/month for 50k. Enterprise: $4,000/month. | The vendor carries the collection risk. Check its terms. https://www.makcorps.com/ |
| **Xotelo** | `https://data.xotelo.com/api` with `/rates?hotel_key&chk_in&chk_out&currency`, `/heatmap`, `/list?location_key&limit≤100`, `/search?query` | Free | Data derives from TripAdvisor. No SLA or rate limits are documented. No AED currency. Use for prototyping only. https://xotelo.com/ |
| **SerpApi Google Hotels** | `serpapi.com/search?engine=google_hotels` with `q, check_in_date, check_out_date, currency, gl, hotel_class, property_token`. Response fields: `properties[].rate_per_night.extracted_lowest`, `prices[].source`, `gps_coordinates`. The 1-hour cache is free. | Developer: $75/month for 5k searches (third-party figure) | This is Google SERP scraping, so the ToS risk sits with the vendor. https://serpapi.com/google-hotels-api |
| **Amadeus Self-Service** | Shut down on 17 Jul 2026; keys are disabled | - | Not an option. Only the Enterprise tier remains. https://github.com/abhinavmathur-atlan/mcp-travel-assistant/issues/4 |
| **Booking.com Demand API** | Approved affiliates only | Free | Its terms prohibit price comparison and forwarding the data, so it cannot be used for rate shopping. https://vorplabs.com/agent-tools/booking-demand-api |
| **STR / CoStar STAR report** | The hotel submits occupancy, ADR and revenue and receives anonymised comp-set indexes | Modest fee | Fully legitimate. https://www.costar.com/products/str-benchmark/resources/faqs |
| **Google** | Travel Insights / Hotel Insights: free search-demand data, **no API (UNVERIFIED)** | Free | The Google-sourced hotel price feed sunsets on **30 Sep 2026**. Independent hotels need a Hotel Center feed or a connectivity partner, or they drop out of Google Hotels. https://www.hotelnewsresource.com/article142219.html |
| **DET via Dubai Pulse** | Dataset `dtcm_hotel_establishment_performance-open`; API key and secret | Free / gov | The portal refused the connection when checked, so **the fields are UNVERIFIED.** https://www.dubaipulse.gov.ae/data/dtcm-general/dtcm_hotel_establishment_performance-open |

**Practical path for a small startup:**
- **MVP:** the hotel's own PMS/channel-manager pace data, plus the PredictHQ Features API, plus the curated venue and holiday tables, plus Xotelo or SerpApi for indicative competitor rates. Label these clearly as "indicative".
- **Production:** apply to the Lighthouse or RateGain partner programmes, or use MakCorps under contract.
- **Easy related feature:** walk hotels through the Google Hotel Center direct-feed setup ahead of the 30 Sep 2026 sunset.
