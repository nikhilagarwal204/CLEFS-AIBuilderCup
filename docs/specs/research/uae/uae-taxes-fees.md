> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

# UAE hotel taxes and fees by emirate (2026): stacking rules, OTA commission treatment and Dubai Tourism Dirham remittance

## 1. Summary table (checked 2026-09-27)

| Emirate | Service charge | Municipality fee | Tourism fee | Per room-night fee | VAT | Confidence |
|---|---|---|---|---|---|---|
| **Dubai** | 10%. This is a hotel's commercial charge, not a government levy. | **7%** of hotel establishment sales. It was cut from 10% by Decree 27/2019 and covers rooms and F&B. | none | **Tourism Dirham (TD)**, by category (see §2) | 5% | Primary. See §5 for 2026 changes. |
| **Abu Dhabi** | 10% (customary) | **4%** "of the value of the invoice issued to the customer" | **4%**, down from 6% on 1 Sep 2023 | **None.** The AED 15/room-night municipality fee was removed on 1 Sep 2023. | 5% | Primary |
| **Ras Al Khaimah** | 10% (customary) | UNVERIFIED (secondary sources say 7%) | UNVERIFIED | Tourism Dirham under Amiri Decree 20/2015, plus a separate "Destination Fee" under Law No. 3 of 2021. Amounts UNVERIFIED: secondary sources give either a flat AED 15, or 20/15/10/7 by category. | 5% | Legal instruments are named on RAKTDA's site; amounts UNVERIFIED |
| **Ajman** | 10% (customary) | UNVERIFIED | **10%** historically. It was cut to 7% for 1 Jul–31 Dec 2018 only. Current rate UNVERIFIED. | UNVERIFIED | 5% | Secondary (news) |
| **Sharjah** | 10% (customary) | UNVERIFIED | UNVERIFIED | UNVERIFIED | 5% | UNVERIFIED |
| **Fujairah** | 10% (customary) | 5% "tourism-establishment" tax on sales, filed monthly. This comes from a secondary source citing the Fujairah Municipality service catalogue. | UNVERIFIED | UNVERIFIED | 5% | Secondary |
| **Umm Al Quwain** | 10% (customary) | UNVERIFIED | UNVERIFIED | UNVERIFIED | 5% | UNVERIFIED |

Sources:
- Dubai TD: https://dlp.dubai.gov.ae/Legislation%20Reference/2014/Executive%20Council%20Resolution%20No.%20(2)%20of%202014.html
- Dubai 7% municipality fee: https://dlp.dubai.gov.ae/Legislation%20Reference/2019/Decree%20No.%20(27)%20of%202019%20Concerning%20Reduction%20of%20and%20Exemption%20from%20Fees%20and%20Fines%20in%20the%20Emirate%20of%20Dubai.html
- Abu Dhabi: https://www.mediaoffice.abudhabi/en/tourism/department-of-culture-and-tourism-abu-dhabi-revises-hotel-fees-to-foster-investment-in-hospitality-sector/
- RAK instruments are listed at https://raktda.com/e-services/. The PDFs could not be fetched (403).
- Ajman: https://gulfnews.com/business/ruler-of-ajman-orders-reduction-of-tourism-fees-1.2246088 and https://www.thenationalnews.com/business/travel-and-tourism/ajman-to-slash-tourist-fees-until-year-end-to-attract-more-visitors-1.746720
- Fujairah (secondary): https://www.bnbcalc.com/blog/short-term-rental-regulation/al-fujairah-city-guide

**Implementation note:** only Dubai and Abu Dhabi can be hard-coded. For Sharjah, Ajman, RAK, Fujairah and UAQ, ship each property's rules as hotel-configured settings with a "verified by owner" flag.

## 2. Dubai Tourism Dirham: exact schedule and rules

This is Schedule (1) of Executive Council Resolution 2/2014. Amounts are AED per room per night.

| Category | AED |
|---|---|
| 5-star or higher hotel/resort | 20 |
| 4-star hotel/resort | 15 |
| 3-star hotel/resort | 10 |
| 2-star hotel | 10 |
| 1-star / budget hotel | 7 |
| Luxury hotel apartment | 20 |
| Deluxe hotel apartment | 15 |
| Standard hotel apartment | 10 |
| Guest house | 7 |
| Luxury holiday home | 15 |
| Standard holiday home | 10 |

Rules from Administrative Resolution 2/2020 (https://dlp.dubai.gov.ae/Legislation%20Reference/2020/Administrative%20Resolution%20No.%20(2)%20of%202020%20Prescribing%20the%20Rules%20for%20Calculating,%20Collecting,%20and%20Paying%20the%20Tourism%20Dirham%20Fee%20in%20the%20Emirate%20of%20Dubai.html):
- **Unit:** charged per hotel room night of the "Main Guest", not per guest. **Multi-bedroom units are charged per room.** This matters for hotel apartments: a 2-bedroom unit owes 2 × the fee.
- **Cap:** a maximum of 30 *consecutive* nights. This was added by Executive Council Resolution 10/2014.
- **When a night counts:** a "Hotel Room Night" starts when check-in is completed. From that, it follows (my inference) that no-shows and cancellation retention do not attract TD.
- **Exemptions:** the hotel's own employees on night-shift duty at that property, and the hotel owner. Documentation must be kept.
- **Disclosure:** TD must be shown clearly in price lists, websites, invoices and receipts, and labelled as a **fee, not a tax**.
- **The "System":** "the electronic system adopted by the DTCM through which the Tourism Dirham Fee is calculated". Guest data must be entered before occupancy, and check-out data follows.
- **Accounts:** financial statements must show TD collected, TD paid and TD still unpaid as separate figures.
- **Remittance:** before the **16th day of the month after collection**, by cash, cheque or electronic transfer. The payment date is the date DET receives the money. Payment details must be confirmed within 24 hours.
- **Penalties** (Resolution 2/2014): fines of AED 1,000–15,000, doubled for a repeat within one year, plus possible closure for up to 3 months.
- **System names:** DET runs "Holiday Homes system / HH 2.0" for holiday homes (https://chargeautomation.com/guest-compliance/uae/). The name of the equivalent reporting system for hotels, and whether it has a public API, is UNVERIFIED. Secondary sources say Opera and IDS Next have direct DTCM interfaces (UNVERIFIED).

## 3. VAT base (what VAT applies to)

- **Tourism Dirham is outside the VAT base.** It is a fixed amount collected on behalf of an authority and is not consideration for the hotel's supply. Sources: https://velmontcrest.ae/insights/tourist-tax-uae/ and https://taxadvisors.ae/guides/hotel-hospitality-tax-uae/ (both secondary). I could not find FTA primary guidance, so treat this as UNVERIFIED-primary.
- **Municipality fee and service charge are inside the VAT base.** VAT is usually calculated on room plus service charge plus municipality fee, because these are part of the consideration (velmontcrest, secondary). I found no FTA primary clarification (UNVERIFIED-primary).
- **Rounding:** exact rounding and whether the municipality fee is charged on (room) or (room + service charge) differ between hotels' PMS setups. The legal base, "Sales of Hotel Establishments" under Regulation 2/2006, does not settle this in the text I could reach.

## 4. How to get from guest-paid gross to room revenue

Definitions:
- G = gross per room-night the guest paid
- TD = per-night fixed fee
- s = 10% service charge
- m = municipality fee % (Dubai 7%; Abu Dhabi 4% municipality + 4% tourism = 8%)
- v = 5% VAT

Three stacking conventions are seen in the market. Store which one each hotel uses.

| Convention | Formula | Dubai factor | Abu Dhabi factor |
|---|---|---|---|
| **A. Compound** (VAT on room + service charge + municipality fee; municipality fee on room + service charge) | R = (G − TD) / ((1+s)(1+m)(1+v)) | 1.10×1.07×1.05 = **1.23585** | 1.10×1.08×1.05 = **1.2474** (UNVERIFIED base) |
| **B.** Municipality fee on room only, VAT on everything | R = (G − TD) / ((1+s+m)(1+v)) | 1.17×1.05 = **1.2285** | 1.18×1.05 = **1.239** |
| **C. Additive** (every percentage applied to the room rate) | R = (G − TD) / (1+s+m+v) | **1.22** | **1.23** |

The Dubai "22% on top" additive shortcut appears at https://www.duettocloud.com/library/uae-hotels-price-right-accommodate-new-tax-laws. Convention C under-charges VAT if VAT legally applies to the service charge and municipality fee.

**Order of operations:**
1. Strip TD first, because it is fixed and has no VAT.
2. Divide by the factor for the hotel's convention.
3. Room revenue (net ADR input) = R. Service charge revenue = sR, which is often treated as revenue, but hotels differ on this.
4. Pass-throughs are the municipality fee, VAT and TD. They are liabilities, not revenue.

**Worked example (Dubai, 3-star, convention A):** the guest pays AED 500 per night, all-inclusive.

| Line | Calculation | AED |
|---|---|---|
| TD | fixed | 10.00 |
| Remaining | 500 − 10 | 490.00 |
| **Room (R)** | 490 / 1.23585 | **396.49** |
| Service charge | 10% × 396.49 | 39.65 |
| Municipality fee | 7% × 436.14 | 30.53 |
| VAT | 5% × 466.67 | 23.33 |
| Check | 396.49 + 39.65 + 30.53 + 23.33 + 10 | 500.00 |

- Under convention C the same stay gives R = 401.64, which is AED 5.15 per night (1.3%) higher. That gap is a detectable mismatch between the PMS, the channel manager and OTA statements.
- If the 2026 Dubai exemption is in force (municipality fee = 0, TD = 0), the factor becomes 1.10 × 1.05 = 1.155, so R = 500 / 1.155 = 432.90.
- A real inclusive rate string for comparison: "Rates are inclusive of 7% municipality fee, 10% service charge, 5% VAT and AED 10 Tourism Dirham Fee" (Rove Dubai, 3-star, https://www.rovehotels.com/en/offer/marjan-monthly-stays/).

## 5. 2026 status changes (important)

- **Dubai, 1 Apr 2026:** a 3-month **deferral**. Hotels, hotel apartments and holiday homes could postpone paying 100% of the sales fees on rooms and F&B, plus the Tourism Dirham. This is a remittance deferral, not a waiver. Source: https://mediaoffice.ae/en/news/2026/april/02-04/dubai-economic-measures-boost-hospitality-and-business-growth
- **Dubai, 21 May 2026:** an AED 1.5bn package that includes "exemption from collection of the Tourism Dirham and the sales fees on hotel rooms and restaurants". It also defers hotel classification fees. Timeframes are "to be announced by the respective government entities", and the package's 33 initiatives run for 3–12 months. Sources: https://mediaoffice.ae/en/news/2026/may/21-05/hamdan-bin-mohammed and https://www.thenationalnews.com/business/economy/2026/05/21/dubai-approves-dh15-billion-relief-package-including-halting-hotel-and-restaurant-tax/
  - **UNVERIFIED:** the exact effective date, the end date, and whether the exemption is in force on 2026-09-27. "Exemption from collection" suggests guests are not charged (my inference).
  - Clefs must support **date-effective rules** (`effective_from` / `effective_to`) and flag stays where the municipality fee or TD was charged or remitted during an exempt window.
- **Dubai, Executive Council Resolution 68/2025:** a 100% **refund** of the municipality fee on room sales and of TD for 2 years (extendable by 1). It applies only to new hotels in Dubai South, Palm Jebel Ali, Dubai Parks and Dubai Islands. Guests are still charged; the hotel gets the money back. Source: https://dlp.dubai.gov.ae/Legislation%20Reference/2025/Executive%20Council%20Resolution%20No.%20(68)%20of%202025%20Financial%20Support%20Initiative%20for%20Hotel%20Establishments.html
- **Abu Dhabi:** I found no 2026 change. The 4% tourism fee plus 4% municipality fee regime has applied since Sep 2023 (UNVERIFIED that nothing changed in 2026).

## 6. OTA handling and commission

**Booking.com General Delivery Terms** (version v2601, https://admin.booking.com/hotelreg/terms-and-conditions.html):
- **Clause 1.1**, the definition of "Taxes", includes municipal and local "fees, (sur)charges and/or levies".
- **Clause 2.3.2(ii):** commission = Room Price per room per night × nights × rooms × commission %, where "the only taxes included in the Room Price shall be value-added tax (VAT), goods and services tax (GST) or any similar sales tax".
  - Implication: **commission is charged on VAT but not on the municipality fee or TD.**
  - This depends on the hotel having set those up as separate charges. If a hotel loads a flat all-inclusive rate with no charge setup, Booking cannot carve them out, and my inference is that commission then falls on the full amount. That is a common source of over-commission.
  - Service charge is not a "Tax", so it is likely part of the commissionable Room Price (inference, UNVERIFIED).
- **Clause 2.3.5:** the hotel is responsible for telling Booking.com about applicable Taxes and any changes to them.

**Booking.com Charges API** (https://developers.booking.com/connectivity/docs/charges-api/manage-property-room-charges and https://developers.booking.com/connectivity/docs/charges-api/charges-meta-endpoint):
- Endpoints:
  - `GET/POST /charges-api/properties/{propertyId}/charges`
  - `GET https://supply-xml.booking.com/charges-api/meta?language_code=EN_GB`
- Charge types:
  - `VAT` (PERCENTAGE mode only)
  - `MUNICIPALITYFEE` (LOCALITY_CHARGES, legacy code 5003)
  - `TOURISMFEE` (OTA code 18)
  - `CITYTAX` (OTA code 3)
  - `GOVERNMENTTAX` (OTA code 46)
  - `SERVICECHARGE` (property charge)
- Fields:
  - `charge_key{type, guest_origin, travel_purpose}`
  - `charge_periods[].applicable.from`
  - `configuration.amount{value, base[], mode}`
  - `configuration.excluded` (boolean)
- `mode` values: `PER_NIGHT`, `PER_STAY`, `PER_PERSON_PER_NIGHT`, `PER_PERSON_PER_STAY`, `PERCENTAGE`, `INCALCULABLE`
- `base` for percentage charges: `NET_ROOM_PRICE`, optionally combined with `PROPERTY_CHARGES` and/or `LOCALITY_CHARGES`. This is how the compound stacking in §4 is expressed; for example, VAT with base `["NET_ROOM_PRICE","PROPERTY_CHARGES","LOCALITY_CHARGES"]`.

**Expedia:** commission and margin treatment of fees is UNVERIFIED (I could not fetch primary docs).

## 7. Common reconciliation errors Clefs should detect

1. **Commission on non-VAT levies.** Compare the OTA commission base with (room + VAT) and flag commission charged on the municipality fee or TD. Check the property's Charges API setup: `MUNICIPALITYFEE` / `TOURISMFEE` present, and the `excluded` flag.
2. **Stacking-convention mismatch** between the PMS, the channel manager and the OTA (A vs B vs C in §4). This shows up as a 0.5–1.3% net ADR drift.
3. **TD per reservation instead of per room.** Hotel apartments often miss the per-bedroom rule.
4. **30-night cap applied to the reservation instead of the continuous stay.** Extensions and split bookings get mis-capped. Relevant sources: https://www.dubaicityguide.com/site/features/12734/tourism-dirham-2.0-in-your-pms---a-2025-compliance-checklist and Administrative Resolution 2/2020.
5. **Wrong TD category after reclassification,** for example standard to deluxe apartment, or 3-star to 4-star.
6. **VAT charged on TD,** which overstates output VAT.
7. **TD double-posted** through packages or manual postings (dubaicityguide).
8. **TD charged on no-shows or cancellations,** where no check-in happened.
9. **Collected vs remitted mismatch:** TD collected in the PMS vs TD paid to DET by the 16th of the following month. The legal ledger fields are *collected*, *paid* and *unpaid*.
10. **Pricing still grossed up during the 2026 exemption.** Municipality fee or TD still charged to guests, or still shown as "excluded" on OTAs, while the exemption window is active.
11. **Exempt nights (staff on night shift, owner) billed,** or billed without documentation.

**Suggested rule table:**
`emirate, property_category, fee_code (SC|MF|TF|TD|VAT|DEST), calc_mode (PERCENT|PER_ROOM_NIGHT), rate, base[], vat_applies, cap_consecutive_nights, per_bedroom, effective_from, effective_to, source_url, verified_by`
