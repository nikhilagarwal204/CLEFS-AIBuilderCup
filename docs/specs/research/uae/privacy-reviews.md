> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

## A. UAE compliance for guest PII and recorded AI conversations

### A1. Federal PDPL (Federal Decree-Law 45/2021)
- **Status:** The law was issued 20/26 Sep 2021 and took effect 2 Jan 2022. The Executive Regulations still look **unissued**:
  - Chambers (updated 10 Mar 2026): "The Implementing Regulations … have yet to be issued." It also says "the Data Office has not yet become fully operational" and that TDRA is the de facto contact. https://practiceguides.chambers.com/practice-guides/data-protection-privacy-2026/uae/trends-and-developments
  - ITSEC: "have not yet been issued … none are listed on the UAE legislation portal (checked 23 September 2026)". https://itsecnow.com/regulators/pdpl-executive-regulations-2026
  - Several SEO/vendor sites say the ERs were "issued in 2026" (one cites "Cabinet Resolution No. 33 of 2024"). **UNVERIFIED and contradicted by the sources above.** Plan for compliance now anyway.
- **Grace period:** Art. 29 gives 6 months after the ERs are issued, and Cabinet can extend it (ITSEC, above).
- **Lawful basis (Art. 4):** Consent is the default. Exceptions include contract performance, legal obligations, the data subject's interests and publicly available data. **There is no legitimate-interest basis.** https://www.dlapiperdataprotection.com/countries/uae-general/law.html
  - For implementation: the booking/stay contract covers operational messages. Marketing, call recording and AI analytics of transcripts need **separate, logged, withdrawable consent**.
  - Detailed consent-form requirements (specific, clear, affirmative, provable, withdrawable) are **UNVERIFIED** (Art. 6 text not fetched).
- **Data subject rights** (all DLA Piper, above):
  - Art. 13: access, free of charge. Covers processing purposes, automated decisions and cross-border recipients.
  - Art. 14: portability, in a "structured and machine-readable manner".
  - Art. 15: erasure.
  - Art. 16: restriction.
  - Art. 17: objection, including to direct marketing.
  - Art. 18: objection to automated decisions with legal effects.
  - Response deadlines are left to the ERs, so they are **UNVERIFIED**.
- **Breach (Art. 9):** Report to the Data Office "immediately upon becoming aware", and notify data subjects where privacy is prejudiced. Exact hours and form are pending the ERs.
- **DPO (Art. 10):** Needed for high-risk technology, systematic assessment/profiling of sensitive data, or large volumes of sensitive data. The DPO can be a contractor and does not need to be UAE-based (DLA Piper).
- **Cross-border (Arts. 22–23):** Transfers are allowed to jurisdictions the Data Office approves as adequate, or under exceptions. Chambers lists one exception as the data subject having "expressly consented in a manner consistent with public order". No adequacy list has been published (**UNVERIFIED** that none exists). US-hosted LLM or telephony processing therefore needs either explicit consent or UAE-region hosting.
- **Carve-outs:** DIFC, ADGM and DHCC free zones; health data under the ICT in Health Fields Law; Central Bank-regulated data; government data (DLA Piper).
- **Penalties:** Deferred to Cabinet. The Cybercrime Law allows fines of AED 50,000–500,000 for illegal collection of personal data (DLA Piper).
- **Recording calls:** Recording a conversation without consent is a criminal privacy offence under the UAE Penal Code (Federal Decree-Law 31/2021, Art. 431). **UNVERIFIED**, from memory. Ship an AI-disclosure and recording notice at the start of every call.
- **Outbound AI calls:** Cabinet Resolution 56/2024 on telemarketing restricts marketing calls. From memory: 09:00–18:00 only, DNCR registry checks, and caps on repeat calls. **UNVERIFIED.** Apply it to any outbound upsell or win-back calling.
- **Children:** Federal Decree-Law 26/2025 on Child Digital Safety applies after a one-year grace period (Chambers).

### A2. DIFC and ADGM
- **DIFC:** DP Law No. 5 of 2020 applies to DIFC entities and to processing in DIFC. Entities file a data protection notification at incorporation and on changes. Regulation 10 (autonomous/semi-autonomous systems, i.e. AI) took effect 1 Sep 2023, with a certification route through accredited bodies. https://www.difc.com/business/registrars-and-commissioners/commissioner-of-data-protection and …/regulation-10
  - Since Jul 2025, data subjects can sue directly in DIFC Courts (Chambers).
  - This is relevant only if Clefs incorporates in DIFC or a client hotel sits inside DIFC.
- **ADGM:** Sep 2025 rules on substantial public interest conditions for sensitive data (Chambers). Low relevance for Dubai hotels.

### A3. Dubai hotel guest registration
- Hotels must capture each guest's ID (passport or Emirates ID) and submit it to the Dubai Police / DET guest registration systems. **UNVERIFIED.** The primary DET/Police pages could not be fetched.
- For implementation: Clefs should **not** become the store of ID scans. Link to the PMS reservation ID. If ID capture is ever added, treat it as a separate high-risk data class with a short TTL.

### A4. Controls a small vendor should ship
These are recommendations built from the obligations above, not quotes from a source.
- **Roles:** Hotel is the controller, Clefs is the processor. Offer a click-through DPA covering:
  - subject matter and duration
  - categories of data subjects (guests, staff, callers)
  - data categories: name, phone, email, stay dates, preferences, call audio, transcripts, review author names
  - purposes, and a sub-processor list with countries (LLM, telephony, cloud, email/WhatsApp)
  - transfer mechanism (explicit consent, or UAE region)
  - security measures (TLS 1.2+, AES-256 at rest)
  - breach notice to the controller within 24–48h, so the hotel can report "immediately"
  - help with data subject requests (DSRs), deletion or return on termination, audit rights, confidentiality
- **Schema:**
  - `consent_records(id, hotel_id, guest_id, channel[voice|whatsapp|email|web], purpose[recording|marketing|ai_analytics|transfer_abroad], notice_version, granted_at, withdrawn_at, evidence_ref)`
  - `dsr_requests(id, hotel_id, subject_identifier, type[access|rectify|erase|restrict|object|portability], status, received_at, due_at, completed_at, export_uri)`
  - `retention_policies(hotel_id, data_class[call_audio|transcript|guest_profile|review|audit_log], ttl_days)`. Suggested defaults: audio 30–90 days, transcripts 12 months, audit logs 1 year or more.
  - A nightly purge job that writes `deletion_log` entries.
  - `audit_log(id, hotel_id, actor_id, actor_role, action, resource_type, resource_id, ip, user_agent, ts)`, append-only.
- **RBAC:** owner, GM, front desk, revenue, read-only auditor. Mask PII fields (phone, email) for lower roles.
- **Other:** PII redaction in transcripts before LLM analytics; per-tenant data export (JSON/CSV) for portability.
- **Data residency options:**
  - **AWS `me-central-1` (UAE):** launched 29 Aug 2022, 3 AZs, RDS/Aurora available. https://aws.amazon.com/blogs/aws/now-open-aws-region-in-the-united-arab-emirates-uae/
  - **Azure UAE North (`uaenorth`, Dubai):** 3 AZs. UAE Central (`uaecentral`, Abu Dhabi) is restricted access. https://learn.microsoft.com/en-us/azure/reliability/regions-list
  - **Supabase:** no Middle East region (16 specific regions, none in ME). https://supabase.com/docs/guides/platform/regions. The feature request for ME regions has had no staff reply since Mar 2025: https://github.com/orgs/supabase/discussions/34551
  - **Neon:** no ME region (8 AWS regions; Azure deprecated). https://neon.com/docs/introduction/regions
  - **Conclusion:** A UAE-resident database means AWS RDS/Aurora in `me-central-1` or Azure Postgres in `uaenorth`. Bedrock and Vertex AI (Gemini) availability in UAE regions is **UNVERIFIED** (Google Cloud's nearest regions are Doha `me-central1` and Dammam `me-central2`).

### A5. India DPDP (brief; not in current scope)
- The DPDP Rules were notified 13 Nov 2025 and phase in as follows:
  - the Data Protection Board provisions took effect immediately
  - consent managers take effect 13 Nov 2026
  - the remaining substantive obligations take effect 13 May 2027
- Breach reporting: a detailed report within 72h. Personal data and logs must be kept at least 1 year for specified purposes.
- Source: https://www.dlapiperdataprotection.com/countries/india/law.html

## B. Review ingestion

### B1. Google Business Profile API: the only full-fidelity, owner-authorised source
- **Access:**
  - Apply through the GBP API contact form ("Application for Basic API Access").
  - Requirements: a GBP that has been verified and active for 60+ days, and a website listed on it.
  - Quota is 0 QPM until approved and 300 QPM after. https://developers.google.com/my-business/content/prereqs
- **OAuth scope:** `https://www.googleapis.com/auth/business.manage`. `plus.business.manage` is deprecated. The location must be verified. https://developers.google.com/my-business/reference/rest/v4/accounts.locations.reviews/updateReply
- **Endpoints** (https://developers.google.com/my-business/content/review-data):
  - `GET https://mybusiness.googleapis.com/v4/accounts/{a}/locations/{l}/reviews`. Params: `pageSize` (max 50), `pageToken`, `orderBy` = `rating` | `rating desc` | `updateTime desc` (default). Response: `reviews[]`, `averageRating`, `totalReviewCount`, `nextPageToken`. https://developers.google.com/my-business/reference/rest/v4/accounts.locations.reviews/list
  - `GET …/reviews/{reviewId}`
  - `POST /v4/accounts/{a}/locations:batchGetReviews`. Params: `pageSize`, `pageToken`, `orderBy`, `ignoreRatingOnlyReviews`.
  - `PUT …/reviews/{reviewId}/reply` (upsert) and `DELETE …/reviews/{reviewId}/reply`.
  - Review fields: `name`, `reviewId`, `reviewer`, `starRating`, `comment`, `createTime`, `updateTime`, `reviewReply`.
- **Push notifications:**
  - `PATCH https://mybusinessnotifications.googleapis.com/v1/accounts/{accountId}/notificationSetting?updateMask=…` with body `{pubsubTopic}`.
  - Grant `mybusiness-api-pubsub@system.gserviceaccount.com` the `pubsub.topics.publish` permission on the topic.
  - New and updated reviews are supported; the enum names `NEW_REVIEW` and `UPDATED_REVIEW` are **UNVERIFIED**.
  - https://developers.google.com/my-business/content/notification-setup
- **Policy constraints** (https://developers.google.com/my-business/content/policies):
  - You need the hotel's authorisation before replying on its behalf.
  - "you must not automate or trigger review replies … without the user's prior specific and express consent". So AI replies should be draft-and-approve by default. Only allow auto-post after an explicit, logged opt-in, stored as `consent_records.purpose = auto_reply`.
  - Cached content "no more than 30 calendar days … cannot be manipulated or aggregated". This conflicts with long-term sentiment analytics. Store derived metrics, set a `reviews.expires_at` TTL and re-fetch. Whether owner-authorised GBP data is exempt is **UNVERIFIED**.

### B2. Google Places API (New): competitor benchmarking only
- `reviews`: "sorted by relevance. A maximum of 5 reviews can be returned."
- Review fields: `name`, `rating`, `text`, `originalText`, `authorAttribution`, `publishTime`, `relativePublishTimeDescription`, `googleMapsUri`, `flagContentUri`, `visitDate`. There is also an AI-generated `reviewSummary`.
- Source: https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places
- **Terms:**
  - No caching except `place_id`, which can be stored indefinitely.
  - Author attribution is required (at minimum the avatar), with a link via `googleMapsUri`.
  - https://developers.google.com/maps/documentation/places/web-service/policies
- **Use:** Show competitor rating and review count live. Do not store the reviews.

### B3. Tripadvisor Content API
- `GET /v1/location/{locationId}/reviews?key=&language=&limit=&offset=` returns "up to 5 of the most recent reviews". Fields include `id`, `published_date`, `rating`, `title`, `text`, `trip_type`, `travel_date`, `user`, `subratings`, and `owner_response`. https://tripadvisor-content-api.readme.io/reference/getlocationreviews
- **Quotas:**
  - 5,000 free calls/month, then tiered billing. https://tripadvisor-content-api.readme.io/reference/faq
  - 50 QPS hard cap; 10,000 search calls/day; detail and review calls limited by your daily budget; 24h rolling window. https://tripadvisor-content-api.readme.io/reference/rate-limits
  - Attribution to Tripadvisor is mandatory.
- **Caveats:**
  - The readme shows a deprecation/migration notice to "Terra"; details are **UNVERIFIED**.
  - Eligibility rules (consumer-facing use only) and any owner-reply API are **UNVERIFIED**.
- **Realistic path:** Owners reply manually in Tripadvisor Management Center. Clefs drafts the reply and gives a copy button.

### B4. Booking.com and Agoda
- **Booking.com:**
  - The Connectivity docs list a **Guest Review API**, plus Messaging, Property scores and Reporting APIs. Access is limited to approved Connectivity Partners. https://developers.booking.com/connectivity/docs
  - Endpoint and field details are **UNVERIFIED** (subpages returned 404).
- **Agoda:** The supply API hub is for partners sending rates, allotment and inventory (ARI). No public review endpoint was found. https://developer.agoda.com/supply/ (review access **UNVERIFIED**)
- **Realistic path for a startup:**
  - Pull reviews through a channel manager or PMS partner that is already a Booking/Agoda connectivity partner, **UNVERIFIED** which ones expose reviews.
  - Or: an owner-uploaded CSV/Excel export from the extranet (**UNVERIFIED** that the export exists), plus parsing forwarded "new review" notification emails.
  - Either way, generate reply drafts for manual posting.

### B5. Scraping
- **Booking.com terms A15.2:** no one may "access, monitor, copy, scrape/crawl … using any robot, spider, scraper … or automated assistants (including … AI-powered assistants)". A15.3 reserves the right to block. https://www.booking.com/content/terms.html
- **Google:** Maps Platform ToS bans scraping (the exact clause number, 3.2.3(a), is **UNVERIFIED**; the page was truncated). https://cloud.google.com/maps-platform/terms
- **UAE law:** The PDPL has a "publicly available personal data" exception (DLA Piper), which may cover reviewer names. The contract/ToS risk and the Cybercrime Law's unauthorised-access risk remain (**UNVERIFIED** how they apply).
- **Recommendation:** Don't scrape. Use:
  1. GBP API (full, with reply)
  2. Places API for live competitor snapshots
  3. Tripadvisor Content API (5 reviews)
  4. OTA reviews via partner, CSV or email ingest

**Suggested tables:**
- `review_sources(id, hotel_id, platform[google|tripadvisor|booking|agoda|expedia|manual], external_location_id, oauth_token_ref, access_mode[api|csv|email|partner], last_synced_at)`
- `reviews(id, hotel_id, platform, external_review_id UNIQUE(platform, external_review_id), rating, title, text, lang, author_display_name, published_at, owner_reply_text, reply_status[none|draft|approved|posted|manual_required], sentiment, topics[], fetched_at, expires_at)`

