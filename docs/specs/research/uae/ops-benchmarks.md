> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

# UAE Hotel Ops Defaults: SLAs, Amenities, Shifts, Service Recovery, Safety, Languages
*Research for Clefs (independent Dubai/UAE hotels, 30–150 rooms). Date: 2026-09-27. Search budget ran out before every item could be checked against a primary source. Anything not confirmed is marked UNVERIFIED.*

---

## 1. Guest-request SLAs and escalation

**What published sources actually say:**
| Item | Benchmark | Source |
|---|---|---|
| Luggage to room | Within 10 min of registration (Forbes) | https://goaudits.com/blog/forbes-standards-unlocking-excellence-and-top-ratings/ (secondary summary of Forbes) |
| Phone answer / hold | 3 rings; hold ≤30 s; checkout ≤5 min (Forbes) | same |
| Room service | Delivered "exactly within the promised timeframe". Forbes publishes no fixed minutes, so the product should promise an ETA and measure against it. | same |
| VIP complaint acknowledgement | 5 min (example given by vendor) | https://www.unifocus.com/service/escalation-sla-management |
| HVAC request resolution | Under 30 min (example given by vendor) | same |
| Updates on long requests | If a request takes more than 30 min, update the guest every 15–20 min. Confirm with the guest within 30 min of completion. | https://hospitality.institute/bha106/efficiently-handling-guest-requests/ |
| Maintenance tiers | **L1 Critical** (lift entrapment, multi-room leak, HVAC loss in extreme heat, gas or electrical hazard): respond ≤15 min, resolve ≤2 h, notify GM immediately. **L2 Urgent** (AC failure in one room, toilet or shower, door lock, no hot water): respond ≤30 min, resolve ≤4 h or move the guest; escalate to the maintenance manager at 2 h. **L3 Standard** (slow drain, light, TV): respond ≤2 h, resolve same shift or next day. | https://oxmaint.com/industries/hospitality/emergency-maintenance-response-for-hotels (vendor) |
| Request log fields | guest name, room, date and time of request, description, expected delivery time, assigned department, time of completion, staff who completed it, notes | https://www.cedarcreeklodges.com.au/assets/pdf/downloads/SOP%20Housekeeping%20-%20Guest%20Request%20Handling.pdf |

No LQA or Forbes minute standards could be found for towels, amenities, turndown or water (LQA's numeric thresholds are paywalled).

**Proposed default `sla_minutes` (ack / done):** These are my UNVERIFIED defaults for a 1–4★ hotel, consistent with the sources above.
- towels_linen 3 / 15
- amenities_toiletries 3 / 15
- drinking_water 3 / 10
- pillows_extra_bed 5 / 20 (extra bed 30)
- ird_standard 2 / 30 (breakfast 25, drinks only 15)
- luggage 2 / 10
- maintenance_ac 5 / 15 to attend, 60 to fix or move the room. Treat as L1 when outdoor temperature is above 40°C.
- maintenance_plumbing 5 / 30
- turndown: scheduled window 18:00–21:00
- housekeeping_makeup_room 5 / 45

**Escalation ladder** (levels and triggers from Unifocus; timings UNVERIFIED):
- **Triggers:** acknowledgement window missed, resolution time exceeded, several interactions without closure, assignee off-shift.
- **Chain:** L0 assignee → L1 dept supervisor at the SLA breach → L2 duty manager/MOD at SLA+10 → L3 GM or owner at SLA+20, or immediately for L1 critical and VIP.
- **Reroute:** add an "alternate department reroute" action when the assignee is unavailable.

## 2. Amenities: par levels, consumption, CPOR, leakage

**Formulas**
- `par = daily_usage × days_of_supply (or lead_time) + safety_stock`
- Worked example: 80 occupied rooms × 2 soaps × 7 days = 1,120, plus a 15% buffer ≈ 1,288.
- Sources: https://www.rongroupglobal.com/opening-a-restaurant/blog-hotel-housekeeping-supplies-procurement-guide.html (formula), search-result summary (worked example)

**Consumption**
- Shampoo runs at about 1 unit per occupied room-night and varies with guest profile. Source: https://pinkantstore.com/blogs/news/hotel-shampoo-a-buying-guide-for-bathroom-amenities (vendor).
- Request rates for dental kits, shaving kits and slippers: UNVERIFIED, no data found. Suggested approach: make them on-request items and learn the rate per 100 occupied room-nights from the hotel's own logs.

**CPOR benchmarks** (US-based; no GCC or India amenity-specific figure found, so GCC is UNVERIFIED)
- All-in CPOR: budget $25–45, midscale $40–65, upscale $65–100, luxury $100–150+. Source: https://www.mylighthouse.com/resources/blog/cost-per-occupied-room-cpor
- Room-attendant labour: $7.32 per occupied room. Minutes per room: RA 24.67, houseperson 9.06, laundry 6.05 (about 0.74 h per occupied room). Source: HotelData 2025, cited at https://rapideyeinspections.com/blog/hotel-housekeeping-cost-per-room/
- Dispenser toiletries about $2.21 CPOR vs individual bottles $3.20–4.10 (midscale). UNVERIFIED: seen only in a search snippet.

**Dubai regulation** (affects the default amenity catalogue)
- 2025 bans: plastic stirrers, cotton buds, straws, polystyrene items.
- From 1 Jan 2026: plastic cups and lids, cutlery and plates.
- Hotels are moving to refillable dispensers.
- Sources: https://www.khaleejtimes.com/uae/dubai-single-use-plastic-ban-final-phase-january-2026-awareness-guide and https://www.dm.gov.ae/2025/12/24/dubai-to-enforce-final-phase-of-single-use-plastic-ban-from-2026/ (that page returned 404 when fetched).
- Product implication: add an `is_single_use_plastic` flag on each amenity SKU.

**Leakage controls** (Rooms Chronicle, W. Frye: https://hospitalitylawyer.com/wp-content/uploads/2019/01/Preventing-hotel-towel-and-ammenity-theft.pdf)
- Floor closets stay locked, preferably with card-key access and an access log. Stock only **1–2 days** of supplies per floor.
- Don't leave fully stocked carts unattended. Use locking carts, or have a houseman restock during the day.
- Replace linen on stayovers **one-for-one**. Record any extras in a **daily linen/amenity disbursement log** (kept in the HK office, or at the front desk in limited-service hotels).
- Count linen from checkout rooms and report discrepancies.
- Use different colours for pool/spa towels. Avoid logos on linens and robes.
- Take inventory monthly. Rotate who counts, and never let someone count their own floor. Keep a linen discard record.
- Look for usage spikes that occupancy doesn't explain.

**Suggested data model:** `issuance(store→floor→cart→room, qty, staff_id, request_id)` plus a `variance = issued − (occupied_rooms × std_per_room + logged_requests)` alert. The "HK control desk handles 15–20 special requests a day in a 100-room hotel" figure is from https://hospitality.institute/bha205/handling-special-guest-requests-at-housekeeping-control-desk/

## 3. Shifts (UAE law) and handover

**Law: Federal Decree-Law 33/2021** (Dubai Development Authority summary: https://dda.gov.ae/-/media/218BD60807934D8F888B472B2FDB78E7.pdf)
- ≤48 h per week and ≤144 h per 3 weeks.
- Overtime ≤2 h per day at **+25%**.
- Work between **22:00 and 04:00 earns +50%, but shift employees are exempt from this premium.**
- Work on a rest day earns a day off or +50%.
- At least 1 weekly rest day.
- Public holiday work earns a day off or +50%.

**Other rules**
- 8 h per day cap, 1 h break, no more than 5 consecutive hours without a break: secondary sources only (https://www.arabianbusiness.com/life/uae-labour-law-2026-for-employees).
- Hotels allowed 9 h per day: UNVERIFIED under the new law (this was in old Law 8/1980; secondary claims only).
- Ramadan: private-sector hours cut by **2 h per day** (6 h/day, 36 h/week). Source: MoHRE 2026 notice at https://www.mohre.gov.ae/en/media-center/news/12/2/2026/reduction-of-working-hours-for-private-sector-employees-by-two-hours-daily-during-the-holy-month-of (connection refused when fetched; confirmed via secondary sources).
- Midday outdoor-work ban: **15 Jun–15 Sep, 12:30–15:00**. Fine is AED 5,000 per worker (secondary source). Source: https://gulfnews.com/uae/government/midday-outdoor-work-ban-returns-across-uae-for-22nd-year-1.500567381. The scheduler should block pool, valet and garden staff during that window.

**Typical patterns** (UNVERIFIED practice; fits the law)
- FO, security and engineering: 3×8 (07–15, 15–23, 23–07).
- HK: day shift 08–17 (9 h span including 1 h break) plus an evening/turndown shift of about 15–24.
- 6-day week (6 × 8 = 48 h).

**Handover log fields**
- Open and overdue requests, rooms in progress or awaiting re-inspection, OOO/OOS rooms, pending maintenance, VIP arrivals, incidents, lost & found.
- Show them automatically to the incoming shift at login. Source: https://oxmaint.com/industries/hospitality/hotel-shift-handover (vendor). Its claim that "34% of maintenance updates are lost without digital handover" is UNVERIFIED.

**Shared floor devices**
- Use MDM, automatic logout and per-user usage tracking. Sources: https://jumpcloud.com/blog/mdm-for-hotels-how-to-secure-mobile-devices and https://www.42gears.com/industries/mdm-for-hospitality/
- Suggestion: switch users with a PIN on a shared device, and attribute every action to `staff_id` rather than `device_id`.

## 4. Service recovery

**Models**
- **LEARN** (Marriott-attributed): Listen, Empathize, Apologize, React/Resolve, Notify. The Notify step includes "what next, by when, and through which channel". Sources: https://umbrex.com/resources/frameworks/marketing-frameworks/service-recovery-frameworks-e-g-learn-last-models/ and https://hrcak.srce.hr/file/266789
- **HEART:** Hear, Empathize, Apologize, Resolve, Thank. Used in Hilton's "Make It Right" guide (https://www.scribd.com/document/1067164091/Guest-Services-Make-It-Right-Service-Recovery). The Broadmoor variant ends with Take action/follow up.

**Compensation**
- Menu of options: F&B or breakfast voucher, handwritten note, late checkout, fruit or dessert amenity, loyalty points, upgrade, complimentary night. Sources: Hilton doc above and https://www.mews.com/en/blog/hotel-service-recovery
- Ritz-Carlton lets staff spend up to $2,000 per guest per incident without approval (secondary, widely cited): https://customersthatstick.com/blog/the-ritz-carltons-famous-2000-rule/
- **Proposed authority matrix** (UNVERIFIED, set per hotel):
  - Line staff: up to AED 100 in amenities or F&B
  - Supervisor: up to AED 300, plus late checkout
  - Duty manager: up to 1 room-night or an upgrade
  - GM/owner: anything above that
- Log `recovery_cost_aed` and `authorized_by`.

**Complaint category → department**
- Cleanliness, linen, amenities → HK
- AC, plumbing, electrical, TV, lock, Wi-Fi → Engineering/IT
- Noise, safety → Security/FO
- Billing, check-in → FO
- Food, IRD delays → F&B
- Staff attitude → dept head plus HR

Emergency work orders are about 15–20% of all work orders but drive 60–70% of complaints (oxmaint, vendor).

## 5. Safety: always send to a human

**UAE numbers**
| Number | Use | Source |
|---|---|---|
| 999 | Police | Wikipedia (https://en.wikipedia.org/wiki/Telephone_numbers_in_the_United_Arab_Emirates) and Gulf News (https://gulfnews.com/living-in-uae/safety-security/essential-emergency-and-support-helplines-in-the-uae-1.500020223) |
| 998 | Ambulance | same |
| 997 | Civil Defence (fire) | same |
| 996 | Coast Guard | same |
| 995 | Search & Rescue | same |
| 991 | Electricity/water failure: DEWA 24/7, also SEWA | same, plus DEWA emergency number via https://www.khaleejtimes.com/uae/dewa-awareness-drive-on-its-hotlines |
| 992 | Water failure in other emirates | same |
| 901 | Dubai Police non-emergency | same |
| 116111 | Child Protection | Gulf News only |
| 800 111 | Dubai domestic violence (DFWAC) | Gulf News only |
| 800 4888 vs 800 4444 | Al Ameen (Dubai confidential crime reporting). **Sources conflict; UNVERIFIED.** | Bayut vs Gulf News |

Note: **996 is the Coast Guard, not electricity or water.** Electricity and water failure is 991.

**Lost passport**
- Get a Dubai Police "Lost Item Certificate" (app or website) and take it to the guest's embassy for an emergency travel document. Source: https://www.dubaipolice.gov.ae/wps/portal/home/services/individualservicescontent/lostitemsDetails/
- Tourist Police mobile +971 50 453 8107 is UNVERIFIED.

**Hard-route triggers** (the AI should never resolve these alone)
- **Immediately page the duty manager and share the number with the guest:** medical symptoms (chest pain, breathing, unconscious, allergy, fall, self-harm), fire, smoke or gas smell, sparks or flooding, violence or weapons, threats, harassment or sexual misconduct (by guests or staff), missing child, theft, lost passport or ID, police involvement, death, lift entrapment, suspected drugs.
- **Hand over to a human (not an emergency):** legal threats, chargeback disputes.
- **Staff safety:** AHLA 5-Star Promise good practice is panic devices for room attendants plus anti-harassment policies in several languages (https://www.ahla.com/5-star). Consider a staff SOS button in the staff app.

## 6. Languages and Arabic voice

**Dubai 2025 visitor mix** (DET via https://dmo.dof.gov.ae/en/news-and-publications/latest-press-releases/dubai-s-tourism-industry-achieves-third-successive-record-breaking-year/)
- 19.59M visitors in total.
- W. Europe 21% · CIS/E. Europe 15% · South Asia 15% · GCC 15% · MENA 11% · NE/SE Asia 9% · Americas 7% · Africa 5% · Australasia 2%.
- Supply and performance: 154,264 rooms in 827 establishments, occupancy 80.7%, ADR AED 579, average stay 3.7 nights.
- Top single markets (older or secondary data): India, then Saudi Arabia, UK, Russia, Oman.

**Suggested languages**
- **Guest-facing, priority order:** English, Arabic, Russian, Hindi/Urdu, Chinese (Mandarin), German, French.
- **Staff-facing** (UNVERIFIED suggestion): English plus Hindi/Urdu, Tagalog, Bengali, Nepali, Malayalam. Basis: Dubai population is about 38% Indian, 17% Pakistani, 7% Bangladeshi, 7% Filipino (secondary: https://www.achat-immobilier-dubai.com/en/nationalities-represented-dubai/).
- Russian and Chinese guests prefer staff who speak their language (https://www.hoteliermiddleeast.com/tourism/russian-dubai-tourists, https://www.thenationalnews.com/uae/from-etiquette-to-language-the-uae-s-hotels-embrace-china-s-visitors-by-the-million-1.752116).

**Arabic voice**
- Gulf speakers use dialect in conversation, not MSA, and often switch between Arabic and English mid-sentence. Gulf pronunciation of ق is "g".
- Speechmatics reports its own error rates (vendor figures): colloquial Arabic 4.5% vs Google 5.9%; Arabic-English code-switching 6.3% vs 9.7%. Its advice is one bilingual model rather than separate dialect packs.
- Sources: https://www.speechmatics.com/company/articles-and-news/your-voice-agent-speaks-perfect-arabic-thats-the-problem and https://munsit.com/blog/arabic-dialect-speech-recognition-gulf-egyptian-levantine
- **Azure:** the `ar-AE` speech-to-text locale supports fast transcription and custom speech (audio plus labelled transcripts, or plain text). Text-to-speech voices are `ar-AE-FatimaNeural` (female) and `ar-AE-HamdanNeural` (male). `ar-SA`, `ar-KW`, `ar-QA`, `ar-OM` and `ar-BH` are also available. Source: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=stt
- **Recommendation** (UNVERIFIED): speak simple Gulf-leaning MSA in replies. Make recognition tolerant of dialect and English mixing. Add a custom vocabulary for hotel terms such as "towel", "AC" and "check-out", which are usually said in English.
