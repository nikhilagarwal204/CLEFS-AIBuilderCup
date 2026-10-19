> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

# Clefs UAE research: completeness critique

**Scope note.** The web-search budget ran out after 0 new searches. The facts added below come from direct fetches of primary or stable pages. Anything I could not re-check is marked **UNVERIFIED**.

---

## 1. Contradictions between the reports and how to resolve them

| # | Conflict | Reports | Resolution for the build |
|---|---|---|---|
| C1 | **Whether Booking.com charges commission on the municipality fee** | ota-formats §1.2 says it is UNVERIFIED and may be a commissionable "fee". uae-taxes §6 cites General Delivery Terms v2601 cl. 1.1 (municipal fees count as "Taxes") and cl. 2.3.2(ii) (only VAT sits in the Room Price). | Follow the GDT, which is the primary source. Commission base = room + service charge + VAT. The 7% municipality fee and the Tourism Dirham (TD) are excluded **only if** the property has set them up as `MUNICIPALITYFEE` / `TOURISMFEE` charges in the Charges API. Rule L1 must check that setup first. The Booking.com help page confirms that the service charge is commissionable (https://partner.booking.com/en-us/help/commission-invoices-tax/commission/understanding-our-commission). |
| C2 | **TD tiers for hotel apartments** | ota-formats §5 says UNVERIFIED. uae-taxes §2 gives the primary schedule. | Use Exec. Council Res. 2/2014 Schedule 1: Luxury HA 20, Deluxe HA 15, Standard HA 10 (https://dlp.dubai.gov.ae/Legislation%20Reference/2014/Executive%20Council%20Resolution%20No.%20(2)%20of%202014.html). |
| C3 | **Whether Dubai fees are live** | The ota-formats worked numbers assume the 7% municipality fee and TD are always charged. uae-taxes §5 flags the 2026 relief. | The fees must be date-effective (see §2a). Do not hard-code the 1.23585 factor without `effective_from/to`. |
| C4 | **L15, "VAT on commission not recovered", listed as a leak** | ota-formats treats it as recoverable money. | The reverse charge is output VAT (VAT201 Box 3) and also recoverable input VAT (Box 10), so for a fully taxable hotel it nets to zero. Treat L15 as a *compliance* flag, not recovery money. Box numbers were not re-checked this session (**UNVERIFIED**). Whether Booking.com, Agoda or Expedia invoice UAE hotels with a UAE TRN (in which case there is no reverse charge) is **UNVERIFIED**. |
| C5 | **Service Control SLA ladder** | The module brief says "3/5/10 min". ops-benchmarks uses per-category ack/done times plus an escalation ladder at breach, +10 and +20. | Keep the two separate. **Ack ladder:** assignee 0–3 min, then supervisor at 3, duty manager/MOD at 5, GM at 10. **Resolution SLA:** per category, taken from the ops table. L1-critical and safety triggers skip the ladder and page a human immediately. All values are configurable per hotel. |
| C6 | **Dubai room supply** | ops: 154,264 rooms in 827 establishments (2025). events: about 149,000 rooms at end-August 2026. | These are different periods and possibly different bases. Do not hard-code either figure. Use them only as narrative context. |
| C7 | **Hours for marketing messages** | whatsapp: 09:00–18:00 under Cabinet Decision 56/2024 (**UNVERIFIED**). SMS: promotional sends 07:00–21:00 (TDRA, per Telerivet). | These are separate regimes. Apply the stricter **09:00–18:00 Asia/Dubai** window to *all* marketing sends on any channel. Utility, transactional and service messages are exempt. |
| C8 | **Whether the PDPL Executive Regulations have been issued** | Vendor sites say they were issued. Chambers (Mar 2026) and ITSEC (checked 23 Sep 2026) say they were not. | Treat them as **not issued**. Build to the Decree-Law articles now: consent is the default and there is no legitimate-interest basis. |
| C9 | **Data residency vs resilience** | privacy: a UAE-resident database means AWS me-central-1 or Azure uaenorth. telephony: me-central-1 lost 2 of 3 availability zones on 2026-03-01, and Vercel dxb1 is under maintenance. | Primary option: Azure `uaenorth` or AWS me-central-1 with cross-region backup. Alternative: host outside the UAE and capture **express cross-border consent** (PDPL Arts. 22–23). Either way, the LLM, telephony and WhatsApp sub-processors (all US-hosted) need the consent record `purpose=transfer_abroad`. |
| C10 | **Long-term review storage** | privacy B1 says GBP content can be cached for no more than 30 days. The `reviews` table design stores text indefinitely. | Store derived metrics (sentiment, topics) permanently. Enforce `expires_at = fetched_at + 30d` on raw text and re-fetch after that. |
| C11 | **Midday outdoor-work ban fine** | ops: AED 5,000 per worker. Gulf News headline: "fines up to Dh50,000". | Probably AED 5,000 per worker capped at AED 50,000 (**UNVERIFIED**). The dates and hours are verified (see §2). |
| C12 | **Vendor-internal conflicts** | PredictHQ post-trial behaviour. Business Calling API status in the UAE (respond.io vs Meta). Twilio UAE voice (guidelines vs pricing page). Al Ameen number (800 4888 vs 800 4444). | Leave these out of the MVP defaults. Do not publish the Al Ameen number. |

---

## 2. Missing UAE facts: what was verified now, and what remains open

### 2a. Dubai fee relief in 2026 (the tax engine depends on this)

**Verified:**
- **Deferral.** "Postpone paying 100% of the sales fees on rooms and food & beverage as well as the Tourism Dirham for three months." It started **1 Apr 2026** and applies to hotels, hotel apartments and holiday homes (https://mediaoffice.ae/en/news/2026/april/02-04/dubai-economic-measures-boost-hospitality-and-business-growth).
  - This changes the remittance *due date* only. Guests were still charged.
  - The end date of about 30 Jun 2026 is inferred (**UNVERIFIED**).
- **Exemption announced 21 May 2026.** "Exemption from collection of the Tourism Dirham and the sales fees on hotel rooms and restaurants." The article gives **no effective dates**; timeframes are to be announced by the responsible entities (https://mediaoffice.ae/en/news/2026/may/21-05/hamdan-bin-mohammed).
  - The National describes it as suspension of "the 7 per cent municipal tax added to hotel and restaurant bills", with the package implemented over 3–12 months (https://www.thenationalnews.com/business/economy/2026/05/21/dubai-approves-dh15-billion-relief-package-including-halting-hotel-and-restaurant-tax/).
  - **Whether the exemption is in force on 2026-09-27 is UNVERIFIED.** The DET newsroom returned 403.

**How to build it:**
- Seed the `fee_rules` rows for Dubai municipality fee and TD with `effective_to = NULL`.
- Add an owner-facing toggle, "Dubai 2026 exemption active from ___", with `verified_by` and `source_url`.
- Show a banner on the Profit Guard and pricing screens until DET publishes dates.
- Store the deferral as a separate `remittance_due_override` rule, not as a change to the charge rate.

### 2b. UAE e-invoicing (none of the reports covered this)

**Verified:**
- The MoF e-invoicing page lists the governing instruments: Ministerial Decisions **243 and 244 of 2025** (Sep 2025), **Cabinet Decision 106 of 2025**, and **Ministerial Resolutions 56 and 66 of 2026** (May 2026).
- It also lists the guidelines: "UAE Electronic Invoicing Guidelines V1.1" (1 Jun 2026) and "Mandatory Field Requirements V1.0" (23 Feb 2026) (https://mof.gov.ae/einvoicing/).
- The official timeline is published only as an image.

**UNVERIFIED (from memory; may have been amended by MR 66/2026):**
- Pilot from 1 Jul 2026.
- Businesses with revenue of AED 50M or more: appoint an Accredited Service Provider (ASP) by 31 Jul 2026 and go live 1 Jan 2027.
- Businesses with revenue under AED 50M: appoint an ASP by 31 Mar 2027 and go live 1 Jul 2027.
- B2C is out of scope at first.
- The model is Peppol "5-corner" with the PINT AE data format.

**Implication:**
- Most hotels with 30–150 rooms fall in the under-AED-50M wave.
- Clefs is not the invoicing system, but OTA commission invoices and corporate folios will arrive as structured PINT AE/UBL.
- Add a `structured_invoice` ingestion adapter to the Profit Guard roadmap.
- Do not build ASP functionality.

### 2c. Other facts verified now

- **VAT:** 5%. Mandatory registration from AED 375,000 and voluntary from AED 187,500; no threshold for non-residents (https://taxsummaries.pwc.com/united-arab-emirates/corporate/other-taxes).
- **Excise tax (minibar and amenity cost):**
  - Energy drinks 100%.
  - Sweetened drinks are tiered by sugar: AED 1.09/L at ≥8 g/100 ml, AED 0.97/L at 5–8 g, AED 0 below 5 g (same PwC source).
  - Minibar cost-per-unit should come from supplier invoices. Do not compute it.
- **Currency:** ISO AED, numeric 784, 100 fils, pegged at **USD 1 = AED 3.6725 since Nov 1997** (https://en.wikipedia.org/wiki/United_Arab_Emirates_dirham). This supports leakage rule L11.
- **Time zone:** `Asia/Dubai`, UTC+4, **no DST** (https://en.wikipedia.org/wiki/Time_in_the_United_Arab_Emirates).
- **Phone numbers:**
  - Mobiles are `+971 5X XXX XXXX`, 9 digits after the country code.
  - Mobile prefixes: 50, 54 and 56 (e&); 52, 55 and 58 (du / Virgin) (https://en.wikipedia.org/wiki/Telephone_numbers_in_the_United_Arab_Emirates).
  - Validation regex: `^\+9715[024568]\d{7}$`.
  - The WhatsApp `to` field drops the `+`.
- **Midday outdoor-work ban 2026:** 15 Jun–15 Sep, 12:30–15:00 daily (https://gulfnews.com/uae/government/midday-outdoor-work-ban-returns-across-uae-for-22nd-year-1.500567381).

### 2d. Gaps that are still UNVERIFIED (could not be checked this session)

- **Name and API of the DET system for reporting TD and guest data from hotels.** Build a TD export (CSV) rather than a direct integration.
- **Dubai Police / DET guest-ID registration rules.** Keep Clefs out of ID storage.
- **FTA primary guidance on whether TD is outside the VAT base.**
- **Tax-invoice rules:**
  - A simplified invoice is allowed at AED 10,000 or less.
  - An invoice must be issued within 14 days of supply.
  - Prices shown to consumers must be tax-inclusive (VAT Decree-Law 8/2017 Art. 38).
  - These are widely cited, but the FTA guide PDF could not be read here.
- **Saudi weekend (Fri–Sat).** Saudi guests are a large part of the GCC share (GCC is 16% of Dubai's source markets, Jan–Aug 2026), so Thu and Fri nights also act as weekend nights.
- **Consumer-protection rules requiring all-in price display in Dubai.**

---

## 3. Top 10 facts a developer must hard-code (as seed data with `source_url`)

1. **Time zone `Asia/Dubai`, UTC+4, no DST.** Every cron job, SLA clock and business date uses this zone. The 07:00 owner brief runs at `0 3 * * *` UTC. [Wikipedia, see §2c]
2. **Money is AED in integer fils (2 decimals), ISO 784.** The USD peg is 3.6725. Every foreign-currency OTA payout is converted at the stated rate, and the difference is logged as L11. [Wikipedia]
3. **Dubai guest-charge stack, date-effective:**
   - 10% service charge (a hotel charge, not a levy).
   - 7% municipality fee (Decree 27/2019).
   - 5% VAT.
   - TD per room per night by category: 5★ 20, 4★ 15, 3★/2★ 10, 1★ 7; Luxury HA 20, Deluxe HA 15, Standard HA 10; guest house 7.
   - Store the stacking convention per hotel as A, B or C. The Convention A factor is 1.23585.
   - Strip TD before dividing by the factor.
   - The 2026 exemption toggle is described in §2a.
   - Sources: [Decree 27/2019](https://dlp.dubai.gov.ae/Legislation%20Reference/2019/Decree%20No.%20(27)%20of%202019%20Concerning%20Reduction%20of%20and%20Exemption%20from%20Fees%20and%20Fines%20in%20the%20Emirate%20of%20Dubai.html), [ECR 2/2014](https://dlp.dubai.gov.ae/Legislation%20Reference/2014/Executive%20Council%20Resolution%20No.%20(2)%20of%202014.html)
4. **TD rules:**
   - Charged per *room* (a 2-bedroom unit pays 2×), not per reservation.
   - Capped at **30 consecutive nights** per continuous stay.
   - Charged only after check-in, so no-shows do not attract it.
   - Staff on night shift and the owner are exempt.
   - Remit **before the 16th of the following month**.
   - The ledger fields are `collected`, `paid` and `unpaid`.
   - Source: [Admin Res 2/2020](https://dlp.dubai.gov.ae/Legislation%20Reference/2020/Administrative%20Resolution%20No.%20(2)%20of%202020%20Prescribing%20the%20Rules%20for%20Calculating,%20Collecting,%20and%20Paying%20the%20Tourism%20Dirham%20Fee%20in%20the%20Emirate%20of%20Dubai.html)
5. **Abu Dhabi stack:** 4% tourism fee plus 4% municipality fee, with no per-night fee since 1 Sep 2023. Other emirates are owner-configured with `verified_by`. [mediaoffice.abudhabi](https://www.mediaoffice.abudhabi/en/tourism/department-of-culture-and-tourism-abu-dhabi-revises-hotel-fees-to-foster-investment-in-hospitality-sector/)
6. **Emergency numbers and hard-route triggers:**
   - 999 police, 998 ambulance, 997 civil defence (fire), 996 coast guard, 995 search & rescue, 991 electricity/water, 992 water.
   - Medical, fire, violence and missing-child triggers bypass the AI and page the duty manager.
   - Source: [Wikipedia](https://en.wikipedia.org/wiki/Telephone_numbers_in_the_United_Arab_Emirates)
7. **OTA deadlines (countdown timers):**

   | OTA | Deadline |
   |---|---|
   | Booking.com | No-show or changes within **48 h after check-out**; dispute handling up to 14 business days ([disputing-your-invoice](https://partner.booking.com/en-us/help/commission-invoices-tax/invoices/disputing-your-invoice)) |
   | Booking.com VCC | Charge within **12 months** of check-out |
   | Expedia EVC | Expires **180 days** after check-out ([EVC doc](https://developers.expediagroup.com/supply/lodging/docs/booking_apis/reservations/learn/evc/)) |
   | Expedia Hotel Collect | Reconcile by the **4th of the following month** |
   | Agoda UPC | Charge within **24 h** |

8. **OTA commission base differs by OTA:**
   - Booking.com: room + VAT only, provided the municipality fee and TD are configured as charges (GDT cl. 2.3.2(ii), [admin.booking.com terms](https://admin.booking.com/hotelreg/terms-and-conditions.html)).
   - Agoda: payout = `RSR − RSR×comp%`, where the RSR includes tax, promotions are applied first, and commission therefore lands on taxes ([Agoda](https://partnerhub.agoda.com/how-do-i-calculate-my-base-rate/)).
   - Expedia: **UNVERIFIED**.
   - `effective_pct = commission_amount / commissionable_price`. Flag any difference over 0.1 pp.
9. **WhatsApp rules:**
   - The owner brief must be a **utility** template (hotel KPIs and decisions only, never upsell) with up to 10 quick replies of up to 25 characters each.
   - Inside the 24 h window: reply buttons (max 3, title ≤20 characters) or a list (≤10 rows).
   - Deduplicate webhooks on `messages[].id`. Verify `X-Hub-Signature-256`.
   - Leave at least 6 s between messages to the same recipient.
   - **Service messages are billable from 2026-10-01.**
   - Marketing sends only 09:00–18:00 local.
   - Sources: [Meta pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages), [components](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/components)
10. **Calendar, labour and consent defaults:**
    - UAE public weekend is Sat–Sun with a Friday half-day. Hotel leisure nights are Fri and Sat. Hijri holidays carry `moon_dependent=true` and ±1 day. Eid Al Etihad is 2–3 Dec 2026.
    - Labour: 48 h/week, overtime +25%; during Ramadan, private-sector hours drop by 2 h/day; the shift scheduler blocks outdoor roles 15 Jun–15 Sep, 12:30–15:00.
    - PDPL: **consent is the default and there is no legitimate interest**. Keep separate, withdrawable consents for recording, marketing, AI analytics and transfers abroad.
    - GBP: **no automated review replies without express consent**; draft-and-approve is the default.
    - Sources: [Gulf News weekend](https://gulfnews.com/uae/saturday-sunday-weekend-from-january-1-in-uae-four-and-half-day-working-week-1.1638864606070), [DDA labour summary](https://dda.gov.ae/-/media/218BD60807934D8F888B472B2FDB78E7.pdf), [DLA Piper PDPL](https://www.dlapiperdataprotection.com/countries/uae-general/law.html), [GBP policies](https://developers.google.com/my-business/content/policies)

---

