> **Note (Oct 2026):** Clefs now runs on Google Gemini: the Gemini API (google-genai SDK) for text, Grounding with Google Search for the AI answer radar, and the Gemini Live API for voice. The third-party findings below are kept as dated research, not as the current stack.

> Research report (webinar review), gathered 2026-09-28. Items marked UNVERIFIED were not confirmed against a primary source.

**Verdict: BUILD-LITE.** Tracking "does an AI assistant recommend my hotel" is already cheap and crowded. The version worth building is a weekly module inside Demand Radar that measures visibility against the comp set, checks AI answers against the hotel's own facts, and sends fixes to the actions ledger. Don't sell it as a standalone "AI rank" product.

## 1. How assistants choose and cite hotels (2025–2026 evidence)

| Study | Method | Key finding | Caveat |
|---|---|---|---|
| [Cloudbeds](https://www.cloudbeds.com/hotel-ai-recommendations/data/) | 810 prompts (15 repeats), 145 properties, 6 cities; three assistants including Perplexity and Gemini | Citations: OTAs 55.3%, hotel sites 13.6%, blogs 19.2%, forums 6%. 72.4% of recommended hotels were brand-affiliated. Gemini leans on Booking.com, Perplexity on Tripadvisor | PMS vendor research |
| [Hotelrank/Sitter AI Mode study](https://nicolassitter.com/research/google-ai-mode-hotel-study-2026) | 4,000 queries via SerpApi `google_ai_mode`, 8 EU cities, Feb 2026 | Citations: meta/review sites 29.2% (Tripadvisor 16.5%), direct sites 21.2%, Google 19.9%, OTAs 17.3%. **Clicks: 79% go to Google Business Profiles**, 16.6% direct, 3.6% OTAs | Vendor; [Lighthouse bought Hotelrank on 29 May 2026](https://www.hotelmanagement-network.com/news/lighthouse-ai-visibility-platform-hotelrank-ai/) |
| [Sitter landscape](https://nicolassitter.com/research/ai-hotel-landscape-2026) | 19,579 runs, 6 models | Grok cites Tripadvisor in 99.9% of runs. Booking.com leads in Gemini (63%) | Vendor |
| [BrightEdge](https://www.brightedge.com/resources/weekly-ai-search-insights/rank-overlap-after-16-months-of-aio) | AI Overviews vs organic, Sept 2025 | Travel has only 23.6% overlap: ranking organically does not mean getting cited | - |

Other things that shape which hotels get shown:
- **Google agentic booking is US-only for now.** It went live in AI Mode on 27 Aug 2026 with Booking, Expedia, Hilton, Marriott and IHG, paid via Google Pay. The hotel or OTA still charges the card ([Skift](https://skift.com/2026/08/27/googles-agentic-hotel-booking-tool-comes-to-ai-mode/)).

**What this means for Dubai independents:** an independent hotel mostly gets surfaced through Tripadvisor, Booking.com, Google Business Profile and its own site. The results lean toward branded hotels, and each engine prefers different sources.

## 2. Measuring it through official APIs

| Engine | How to call it | Citation fields | Localisation | Price |
|---|---|---|---|---|
| Gemini | tool `google_search` | `url_citation` annotations, `google_search_call.queries`; `search_suggestions` HTML must be displayed per the ToS ([docs](https://ai.google.dev/gemini-api/docs/google-search)) | - | Gemini 3.x: 5,000 free searches/month, then $14 per 1k *queries* (one prompt can run several). Gemini 2.5: $35 per 1k prompts ([pricing](https://ai.google.dev/gemini-api/docs/pricing)). [Maps grounding](https://ai.google.dev/gemini-api/docs/maps-grounding) also exists; its price is UNVERIFIED |
| Perplexity Sonar | chat completions | `search_results[]` {title, url, date, snippet}; the old `citations` field has been removed ([docs](https://docs.perplexity.ai/docs/sonar/quickstart)) | - | $1/$1 per M tokens plus $5/$8/$12 per 1k requests ([pricing](https://docs.perplexity.ai/getting-started/pricing)) |
| Anthropic | `web_search_20250305` / `_20260318` | `web_search_result_location` {url, title, cited_text ≤150 chars} ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)) | `user_location` (approximate) | $10 per 1k searches plus tokens |
| Google AI Mode (SerpApi) | `engine=google_ai_mode` with `q`, `location`/`uule`, `gl`, `hl`, `device` | `text_blocks`, `references`, `reconstructed_markdown` ([docs](https://serpapi.com/google-ai-mode-api)) | location / gl / hl | 1 credit per successful request; cached results are free ([SerpApi](https://serpapi.com/blog/calculating-search-credit-usage-with-serpapi/)). Plans run $25/1k to $150/15k ([pricing](https://serpapi.com/pricing)). AI Mode's exact credit cost is UNVERIFIED |
| Google AI Overviews (SerpApi) | `engine=google_ai_overview` with a `page_token` from a normal Google search | `text_blocks`, `references` | via the parent search | ~2 requests per check (token expires in 1 minute) ([docs](https://serpapi.com/google-ai-overview-api)) |
| Meta AI, Grok, consumer Claude apps | No official parity API. Scraping the apps is a ToS risk | - | - | Not recommended for v1. xAI API search: UNVERIFIED |

**API answers are not app answers.** An [audit paper (arXiv 2609.18729)](https://arxiv.org/abs/2609.18729) found:
- Overlap was 14.8% on Gemini's API.
- Its conclusion: "neither isolated responses nor API observations can be assumed to represent" what consumers see.

Otterly adds that system prompts and settings differ between app and API.

**How to sample:**
- **Prompts:** 15–25 per hotel. Mix generic ("hotel apartments Al Barsha"), persona ("family, near Dubai Mall"), event-driven (from Demand Radar, e.g. "near DWTC during GITEX"), Modern Standard Arabic versions, and fact checks ("does X have parking / what time is check-in").
- **Repeats:** at least 4–5 runs per prompt per engine per week. The published studies used 15–25 ([Cloudbeds](https://www.cloudbeds.com/hotel-ai-recommendations/data/), [Sitter](https://nicolassitter.com/research/google-ai-mode-hotel-study-2026)).
- **Locales:** run as the UAE, plus the hotel's top 2 source markets from the PMS.
- **Metrics:**
  - Mention rate with a Wilson confidence interval.
  - Share of voice against the Demand Radar comp set.
  - Average position in the list.
  - Mention vs citation, tracked separately.
  - Which sources were cited when the hotel won or lost.
  - Fact errors against Clefs's own data.
- **Stamp every run** with model ID, locale and timestamp, and keep the raw JSON.

**Cost (my estimate, not verified):** about 320 runs per engine per month per hotel, roughly $10–25 per hotel per month across 4 engines. Gemini's free 5k searches/month are shared across the whole account.

## 3. Existing tools and pricing

| Tool | Price | Notes |
|---|---|---|
| Rank My Hotel (the webinar tool) | ₹9,999/yr, 100 checks/month ([site](https://rank.hotelgrowthhub.com)) | The public page describes Google search and Maps rank checks plus an AI rank-check add-on. **It does not describe checking AI assistants; that claim is UNVERIFIED** |
| [Otterly](https://otterly.ai/pricing) | $29 / $189 / $489 per month for 15 / 100 / 400 prompts | AI Overviews, Perplexity, Copilot. AI Mode and Gemini are $9–149 add-ons |
| [Semrush](https://www.semrush.com/pricing/ai/) | $99/month per domain, billed annually, 25 prompts | - |
| Peec AI | €85 / €205 / €425 per month ([third-party](https://www.workduo.ai/blog/peec-ai-pricing)) | Not on the primary page: UNVERIFIED |
| Ahrefs Brand Radar | ~$199/month plus a $129 base plan ([third-party](https://www.tryanalyze.ai/blog/ahrefs-brand-radar-review)) | UNVERIFIED |
| [Profound](https://www.tryprofound.com/pricing) | Enterprise, custom pricing | 9 engines |
| [Local Falcon](https://www.localfalcon.com/pricing) | $24.99–199.99/month in credits | Grid-based checks across AI Overviews, AI Mode, Gemini, Grok ([blog](https://www.localfalcon.com/blog/now-live-track-your-ai-visibility-in-ai-mode-gemini-and-grok-with-local-falcon)) |
| **Lighthouse + Hotelrank** | Not disclosed | Gemini; visibility score; direct vs OTA link split ([source](https://www.hotelmanagement-network.com/news/lighthouse-ai-visibility-platform-hotelrank-ai/)) |
| **Cendyn Wayfinder** (June 2026) | - | A "Fact Engine" checks AI answers weekly against the hotel's CMS content, plus rate-parity checks ([HotelMgmt](https://www.hotelmanagement.net/tech/cendyn-rolls-out-new-ai-analytics-and-monitoring-platform)) |
| Mirai | - | Conversational booking layer, not monitoring ([TDN](https://www.traveldailynews.com/technology/mirai-introduces-conversational-ai-infrastructure-for-direct-hotel-bookings/)) |
| D-EDGE, Revinate | - | No AI-visibility monitor found (UNVERIFIED) |

## 4. What a hotel can do to improve AI visibility

| Lever | Evidence | Strength |
|---|---|---|
| Review volume, rating and recency on Tripadvisor, Booking and Google | Every recommended property had strong ratings and high review volume ([Cloudbeds](https://www.cloudbeds.com/hotel-ai-recommendations/data/)); Tripadvisor is 16.5% of AI Mode citations | Medium-high (correlation only) |
| Google Business Profile completeness and booking link | 79% of AI Mode hotel clicks go to GBP ([Sitter](https://nicolassitter.com/research/google-ai-mode-hotel-study-2026)) | High for Google surfaces |
| OTA listing content (Booking.com especially) | Booking.com leads citations in Gemini | Medium |
| Own-site facts that are indexed and eligible for a snippet | Google: "no additional requirements… nor other special optimizations" ([Google](https://developers.google.com/search/docs/appearance/ai-features)) | Medium |
| Hotel schema.org JSON-LD | Google says no special schema is needed. Only 10.6% of hotels have good markup ([Sitter](https://www.nicolassitter.com/research/hotel-schema-adoption-study-2026)). Evidence of impact is mixed. Rules on self-serving `aggregateRating`: UNVERIFIED | Low-medium; worth doing as cheap hygiene |
| llms.txt | 97% of files never get read ([Ahrefs](https://ahrefs.com/blog/llmstxt-study/)); Google says it is not needed | Reject |
| Wikipedia/Wikidata | Citation rates swing with model version; most 30–150-room hotels won't meet Wikipedia's notability bar | Low |
| Reddit and forums | Volatile, and astroturfing is risky | Low |
| Editorial and local press | 10.9% of AI Mode citations | Low-medium |
| Site speed | No evidence found | UNVERIFIED |

## 5. UAE specifics

- **AI adoption:** 73.3% of the UAE's 15–64 population used generative AI in Q2 2026, the highest in the world ([Microsoft](https://blogs.microsoft.com/on-the-issues/2026/09/21/the-continued-state-of-global-ai-diffusion-in-2026/)).
- **AI assistant usage:** 60% of 18–24s and 50% of 25–34s use AI assistants weekly.
- **Travel planning** ([Marriott Bonvoy survey](https://marriott.pressarea.com/en/news/two-thirds-of-travellers-from-the-united-arab-emirates-and-saudi-arabia-would-trust-ai-to-book-holiday-accommodation-while-lux-scaping-and-passion-pursuits-are-revealed-as-key-travel-trends-for-2026/27112025), 4,000+ adults in UAE and KSA):
  - 77% of UAE residents have used AI to plan a trip.
  - 71% say they would be comfortable booking accommodation through AI.
- **Google surfaces:** AI Overviews are live in the UAE ([Gulf News](https://gulfnews.com/technology/have-you-seen-it-googles-ai-overviews-are-now-in-the-uae-1.500135889)). AI Mode added Modern Standard Arabic ([Arab News](https://www.arabnews.com/node/2618104/media)). Agentic booking has not reached the UAE yet.

## 6. Recommended "Visibility Radar" for Clefs

**What it does**
1. **Weekly run** on Gemini (Search grounding), Google AI Mode (SerpApi) and Perplexity Sonar. Leave out Claude, Grok and Meta AI in v1.
2. **Prompts generated automatically** from the hotel profile, the Demand Radar comp set and upcoming events. Run in English and Arabic, as the UAE plus the top source markets.
3. **Outputs:**
   - Share of voice against the comp set, with confidence intervals.
   - Average position.
   - Source mix (e.g. "when competitors win, Booking.com is cited 45% of the time").
   - **Fact errors** compared with Clefs's own data (check-in time, parking, star rating, "closed", price band).
4. **Every finding becomes an actions-ledger item.** Examples: fix a Booking.com amenity, update GBP attributes or the booking link, reply to unanswered reviews (via Reviews-lite), add a missing FAQ. Re-check afterwards to verify the fix.
5. **Owner brief:** one line in the weekly brief, not daily, and only when a change falls outside the confidence interval for 2+ weeks.
6. **Lead magnet:** a free "AI snapshot" (5 prompts × 3 engines × 3 runs) offered next to the OTA-statement audit.

**Keeping the numbers trustworthy**
- Label results "API simulation; consumer app answers vary" and cite the arXiv audit.
- Keep the raw response, model ID, locale and citations as evidence behind every number.
- Minimum sample size before showing any figure. No single-run screenshots.
- Flag model-version changes, and reset baselines after shifts like the Reddit drop.
- Report "movement vs comp set", never a causal "AI uplift" claim.
- Add it to Trust evals: golden prompts, and parser accuracy on citations.

**What is commoditised vs defensible**
- **Commoditised:** raw prompt tracking. Otterly starts at $29, Local Falcon at $25, Semrush at $99, and Lighthouse and Cendyn now bundle it for hotels.
- **Defensible for Clefs:**
  - Checking AI answers against PMS and ops data the hotel already holds.
  - Fixing the problem through the actions ledger across OTA content, GBP and reviews.
  - Connecting to Profit Guard: AI clicks go to GBP, GBP's booking link can go direct, and direct bookings cut OTA commission.
  - Arabic prompts and UAE source-market locales.
  - Reusing the Demand Radar comp set.

**Reject:** llms.txt generators, "guaranteed AI rank" claims, scraping consumer apps, and seeding Reddit.
