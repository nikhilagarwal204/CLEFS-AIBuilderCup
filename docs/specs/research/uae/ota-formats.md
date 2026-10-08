> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

# OTA Profit Guard: research on Booking.com, Agoda and Expedia exports, statements and payouts

**Coverage note.** partner.booking.com help pages, the Expedia developer docs (they redirect to a login portal) and most Expedia Partner Central help returned 403 errors or redirects. Facts from those pages come from search-engine extracts of the primary URL, and they are cited to that URL. Agoda Partner Hub and developers.booking.com fetched directly. None of the three OTAs publishes its exact CSV column headers. The ingestion layer therefore needs header-alias mapping and cannot rely on fixed column positions.

---

## 1. Booking.com

### 1.1 Reservation export and reservation statement
- **Where to get it:** Extranet → Finance → Reservation statements. Pick an invoice, then Actions → "View statement". Files come from "Download XLS" / "Download CSV". Each statement lists the reservations in a monthly commission invoice together with their commission amounts. ([reviewing-your-reservation-statement](https://partner.booking.com/en-us/help/commission-invoices-tax/invoices/reviewing-your-reservation-statement))
- **Invoice total vs remittance total:** the invoice total only includes amounts Booking.com charges commission on, such as room rates. The total on the remittance advice is everything Booking.com collected from guests. These two numbers are never expected to match. ([same](https://partner.booking.com/en-us/help/commission-invoices-tax/invoices/reviewing-your-reservation-statement))
- **Timing and corrections:**
  - Invoices are emailed in the first week of each month and must be paid within 14 days.
  - A wrong invoice is not reissued. Booking.com issues a credit note or debit note instead.
  - Other "additional invoices" exist: customer complaint, incorrect reporting, debit notes and credit notes.
  - ([paying-your-invoices](https://partner.booking.com/en-us/help/commission-invoices-tax/invoices/paying-your-invoices), [understanding-additional-invoices](https://partner.booking.com/en-us/help/commission-invoices-tax/invoices/understanding-additional-invoices))
- **Disputes:**
  - Changes should be marked within 48 hours of check-out. Otherwise full commission is charged.
  - Commission can only be disputed once the reservation has been invoiced. The flow is: select the reservation → "Dispute commission amount" → "Dispute reservation" in the Remarks column → pick a reason and enter the amount the guest paid.
  - Processing takes up to 14 business days.
  - ([disputing-your-invoice](https://partner.booking.com/en-us/help/commission-invoices-tax/invoices/disputing-your-invoice))
- **No-shows:** "Mark as no-show" is available from midnight on the check-in date until 48 hours after check-out. If the property waives the fee, no commission is charged. ([reporting no-shows](https://partner.booking.com/en-us/help/reservations/overbookings-no-shows/reporting-guest-no-shows-your-property)) The API equivalent is `is_no_show` with `waived_fees=yes|no`. ([B.XML reporting](https://developers.booking.com/connectivity/docs/reporting-api/b_xml-reporting))
- **UNVERIFIED column headers** (from practitioner memory; build an alias map for them):
  - Reservations download: `Book number, Booked by, Guest name(s), Check-in, Check-out, Booked on, Status, Rooms, Persons, Adults, Children, Price, Commission %, Commission amount, Payment status, Payment method, Remarks, Booker group, Booker country, Travel purpose, Device, Unit type, Duration (nights), Cancellation date`.
  - Status values: `ok / cancelled_by_guest / cancelled_by_hotel / no_show`.

### 1.2 What commission is charged on
- **City tax:** no commission on local taxes such as city tax.
- **VAT/GST:** commission is charged on VAT/GST "in most countries", depending on local law.
- **Extra fees:** added fees such as cleaning and service charges are commissionable.
- Tax settings are under Property → VAT/Tax/Charges.
- ([understanding-our-commission](https://partner.booking.com/en-us/help/commission-invoices-tax/commission/understanding-our-commission))
- **UAE mapping is UNVERIFIED:** whether the Dubai 10% service charge and 7% municipality fee count as commissionable "fees", and whether the Tourism Dirham counts as a non-commissionable "city tax", depends on how each is set in VAT/Tax/Charges. The engine must read that per-property configuration.

### 1.3 Payments by Booking.com (payouts and virtual cards)
- **Payout methods:**
  - Bank transfer: daily (1 day after check-out), weekly (every Thursday) or monthly (the 1st).
  - Virtual credit card (VCC), issued per booking.
  - Stripe (US/Canada only).
  - ([understanding-payouts](https://developers.booking.com/connectivity/docs/payments-by-booking-onboarding-api/understanding-payouts), [FAQ](https://partner.booking.com/en-us/help/policies-payments/payment-products/payments-bookingcom-faqs))
- **Gross vs net:** a net payout already has commission and the payment-service fee deducted. VCC payouts are "almost always gross". Bank transfers are usually paid "in your local currency". The payment charge has applied since 1 June 2019 and is shown on invoices. ([understanding-payouts](https://developers.booking.com/connectivity/docs/payments-by-booking-onboarding-api/understanding-payouts), [how-much-it-costs](https://partner.booking.com/en-us/help/policies-payments/payment-products/how-much-payments-bookingcom-costs)) The fee rate itself is UNVERIFIED.
- **VCC activation codes:**

  | Code | Card activates |
  |---|---|
  | 1 | on check-out |
  | 2 | when the booking becomes non-refundable |
  | 3 | on check-in |
  | 4 | 1 day after check-in |
  | 5 | "long after check-out" |
  | 6 | on the cancellation date |
  | 7 | on the booking date |

  ([understanding-payouts](https://developers.booking.com/connectivity/docs/payments-by-booking-onboarding-api/understanding-payouts))
- **VCC expiry:** a card must be charged within 12 months of check-out, after which the funds are lost and no card is reissued. It can be charged several times until the balance is zero. ([VCC help](https://partner.booking.com/en-us/help/policies-payments/payment-products/everything-you-need-know-about-virtual-credit-cards))
- **Payments API fields** (use these as the canonical schema) ([managing-payment-and-payout-details](https://developers.booking.com/connectivity/docs/payments-api/managing-payment-and-payout-details)):
  - Endpoints:
    - `GET /connectivity-payments/reservations/{id}`
    - `…/{id}/breakdown`
    - `…/{id}/payout/bt`
    - `…/{id}/payout/vcc?vcc-id=`
  - Reservation and guest-payment fields: `payout_type` (GROSS|NET|UNKNOWN), `payout_status` (PENDING|PAID|PARTIALLY_PAID|CANCELLED|UNKNOWN), `total_price_of_reservation.total_amount_paid`, `total_amount_to_collect_at_property`.
  - Partner payout fields: `partner_payout.total_payout`, `commissionable_price`, `commissions_and_charges.breakdown.total_commissions/total_charges`.
  - Price breakdown fields: `price_breakdown[].charges[]{type, amount, is_already_collected_from_guest, is_included_total_partner_payout, applicable_rate, applicable_type}`.
  - Bank transfer fields: `{amount, status: PENDING|PAID|UNKNOWN, payout_date, bank_transfer_reference}`.
  - VCC fields: `{id, activation_date, expiration_date, current_balance, status: AVAILABLE|FUNDED|NOT_LOADED|PARTIALLY_CHARGED|FULLY_CHARGED|CANCELLED}`.
  - Every money field is `{value, currency, decimals}`.
  - No exchange-rate field is documented.
- **B.XML reservation fields** ([managing-reservations-bxml](https://developers.booking.com/connectivity/docs/reservations-api/managing-reservations-bxml)):
  - Money: `totalprice`, `commissionamount`, `currencycode`.
  - Per-night `price` has attributes `date, rate_id, genius_rate, rewritten_from_id/name`.
  - Promotions and status: `promotion_id`, `booker_is_genius`, `status` (new|modified|cancelled).
  - Payment: `payout_type` ("BankTransfer" | "Virtual credit card"), `cc_activation_date`, `vcc_expiration_date`, `cc_current_balance`.

### 1.4 Discount programmes and commission uplift in the data
| Programme | Mechanics | Where it shows up in the data |
|---|---|---|
| Genius | Discount funded by the hotel (10–20%), no extra commission ([houst](https://www.houst.com/blog/booking-com-genius-program), secondary source) | `genius_rate`, `booker_is_genius` |
| Mobile rate | Discount of at least 10%; stacks with Genius, not with Country rate or Limited-time deal ([setting-mobile-rates](https://partner.booking.com/en-us/help/rates-availability/rates-special-offers/setting-mobile-rates), [rentalsunited](https://rentalsunited.com/blog/get-more-bookings-on-booking-com-with-mobile-and-country-rates/)) | `rate_id` / `promotion_id`; UNVERIFIED as an export column |
| Country rate | Stacks with Genius and other promotions except Mobile and Limited-time deal ([setting-country-rates](https://partner.booking.com/en-us/help/rates-availability/rates-special-offers/setting-country-rates)) | "Booker country" (UNVERIFIED) plus the rate |
| Early Booker | Booked at least 15 days before arrival ([early-booker](https://partner.booking.com/en-us/help/rates-availability/rates-special-offers/setting-early-booker-rate-plan)) | `rewritten_from_id` / rate plan |
| Preferred / Preferred Plus | About +3% and about +8% commission (secondary sources: [houst](https://www.houst.com/blog/booking-com-fees-for-hosts); [PPP page](https://partner.booking.com/en-us/help/growing-your-business/increase-revenue/all-you-need-know-about-preferred-partner-program)) | Higher "Commission %" per reservation |
| Visibility Booster | Extra commission % chosen per date and booker country ([VB page](https://partner.booking.com/en-us/help/growing-your-business/increase-revenue/all-you-need-know-about-visibility-booster)) | "Commission %" above the contract base for the targeted stay dates |

- **Detection:** `effective_pct = commission_amount / commissionable_price`. Compare it to the contract base plus the uplift that was active for that stay date and booker country.
- **Stacking check:** when more than one discount applies, `discount_depth = 1 − price / public_price`. Flag any stacking deeper than the owner's configured cap.

---

## 2. Agoda (YCS / Partner Portal)

- **Menu paths:**
  - Bookings → Reservations, with tabs All / Confirmed / Amended / Cancelled and a filter for "Rate plans". Export is "Download (.csv)". ([ASO tracking](https://partnerhub.agoda.com/how-do-i-track-agoda-special-offers-bookings/))
  - Reports → Bookings, which can be filtered by payment model. ([identify PC](https://partnerhub.agoda.com/how-do-i-identify-property-collect-bookings/))
  - Finance → All transactions. Filters: date type (booking/check-in/check-out), a "Manual adjustments" toggle and Booking ID. Statuses: Pending departure / Departed / Approved / Disputed / Paid.
  - Finance → Past payouts, grouped by payout ID. ([viewing payouts](https://partnerhub.agoda.com/how-do-i-view-booking-transactions-and-past-payments-for-epass-payments/))
  - Finance → Remittances → batch → Download (PDF). This is the Property Collect statement: the amount the guest paid plus the commission deducted. ([PC statement](https://partnerhub.agoda.com/where-do-i-get-the-property-collect-booking-statement/))
  - Finance → Contracted compensation (the commission rate). ([commission](https://partnerhub.agoda.com/what-commission-do-i-pay-to-agoda/))
- **Payment models:**
  - Agoda Collect bookings are shown as "PREPAID".
  - Property Collect bookings are shown as "TO BE PAID BY GUEST". Their commission is offset in ePass against prepaid payouts, and there is no invoice. ([identify PC](https://partnerhub.agoda.com/how-do-i-identify-property-collect-bookings/), [handle PC](https://partnerhub.agoda.com/how-do-i-handle-property-collect-payments/))
  - Negative balances arise from commission owed or refunds, and are offset against other bookings. ([payouts](https://partnerhub.agoda.com/how-do-i-collect-earnings-from-my-bookings/))
- **Payout rails:**
  - Bank transfer: 3–5 business days.
  - UPC on ePass: must be charged within 24 hours, otherwise wait 72 hours for a reissue.
  - Agoda Card: charge on the card terminal within 30 minutes.
  - ([payouts](https://partnerhub.agoda.com/how-do-i-collect-earnings-from-my-bookings/))
- **Payout formula:** `Base = RSR − RSR × comp%`. Promotions reduce the reference sell rate first. Example: rate 100 with a 10% promotion gives RSR 90; 20% commission of 18 leaves 72. The reference sell rate includes taxes and fees, so commission is effectively charged on tax-inclusive amounts. ([base rate](https://partnerhub.agoda.com/how-do-i-calculate-my-base-rate/))
- **When commission applies:** on stays, on partly or fully non-refundable bookings, and on no-shows or cancellations where the guest was charged. It does not apply when the fee is waived or the cancellation was free. ([commission](https://partnerhub.agoda.com/what-commission-do-i-pay-to-agoda/))
- **AGX (Growth Express):**
  - An extra "cost per booking" on top of contracted commission, charged only on departed bookings.
  - A cancelled booking is refunded if paid by Credit.
  - It is deducted either by "payout reduction" or from Credits.
  - ([AGX](https://partnerhub.agoda.com/what-is-agoda-growth-express/))
- **VIP and promotions:**
  - VIP Silver / Gold / Platinum. Joining requires a discount of at least 15% ([press](https://www.agoda.com/press/agoda-introduces-agodavip-program-to-boost-sales-for-hotel-partners/)).
  - Stacking rules: Essential promotions stack with each other. Targeted promotions stack across categories but not within one. Seasonal promotions do not stack with other Seasonal ones. ([promotions](https://partnerhub.agoda.com/what-are-promotions/))
- **UNVERIFIED:** the CSV column names (for example Booking ID, Customer name, Check-in/out, Room type, Rate plan, Payment model, Total inclusive, Commission, Net inclusive, Currency) and the payout currency.

---

## 3. Expedia (Partner Central)

- **Models:**
  - Expedia Collect: Expedia takes the guest's payment and pays the hotel the net amount, by virtual card (EVC) or bank transfer.
  - Hotel Collect: the hotel charges the guest, and Expedia invoices commission monthly under Finances → Billing & Payments → Invoices. Invoices are due on receipt, with a late charge of 2% after 15 days.
  - ([search extract of ETP T&Cs](https://b.travel-assets.com/epc/epc-onboarding-web/b6adb97a018a8bd1366469ba8fee376ec7ddd6c1/pdf/termsAndConditions/emea/etp/237/emea_etp_237_1033.pdf); the PDF itself could not be parsed, so treat the exact figures as UNVERIFIED)
- **Reconciliation:**
  - Hotel Collect bookings can be reconciled from 5am on the day after check-in until the 4th of the month after check-out (for example, check-out 2 March → deadline 4 April).
  - If not reconciled in time, the invoice includes the booked amount.
  - The API allows only one reconciliation per booking. A no-show is reconciled with the penalty that was actually charged, and the fee cannot exceed the booking amount.
  - ([cancelReservationReconciliation](https://developers.expediagroup.com/supply/lodging/docs/booking_apis/reservations/lsgql_reference/cancelReservationReconciliation/), [Beds24](https://wiki.beds24.com/index.php/Setting/Report_cancel_or_no_show_to_Expedia))
  - Cancellations before check-in waive all penalties. No-show and post-check-in reconciliation is available for Hotel Collect only.
  - The GraphQL API has a `businessModel` field (EXPEDIA_COLLECT / HOTEL_COLLECT) and a `reconciliationEligibility` object ([intro](https://developers.expediagroup.com/supply/lodging/docs/booking_apis/reservations/getting_started/intro/)).
- **EVC:**
  - Activates on the check-in date and cannot be charged before it. It is charged at check-out.
  - A cancellation penalty can be charged on the original arrival date.
  - The card expires 180 days after check-out.
  - An expired card needs an Expedia support case.
  - ([EVC dev doc](https://developers.expediagroup.com/supply/lodging/docs/booking_apis/reservations/learn/evc/))
  - Partner Central has "ready to charge" and "refunds" queues for virtual cards ([github skill](https://github.com/jeanea-ai/expedia-virtual-card-reconciliation)).
  - Settlement reaches the bank 1–2 days after the charge ([RateGain](https://rategain.com/blog/streamlining-payments-with-expedia-virtual-card-for-hoteliers/), secondary source).
- **Uplifts:**
  - Accelerator: the hotel sets a higher commission and pays it only on completed stays in the chosen dates ([EG blog](https://partner.expediagroup.com/en-us/resources/blog/travelads-and-accelerator-hotel-visibility-boosters)).
  - Member Only Deals: discount of at least 10% (secondary source, [houst](https://www.houst.com/blog/expedia-advertising)).
  - TravelAds: pay-per-click, billed separately (UNVERIFIED).
- **UNVERIFIED:** the column headers of the reservations report (Partner Central does offer "Download a reservations report").

---

## 4. Revenue-leakage taxonomy and detection rules

| # | Leak | Detection from exports | Formula / rule |
|---|---|---|---|
| L1 | Commission charged on non-commissionable taxes (Tourism Dirham, city tax) | Statement commission ÷ PMS room revenue excluding those taxes | `overbill = comm_amt − pct × commissionable_base` (base per OTA config, §1.2) |
| L2 | Commission % above contract or uplift | `comm_amt / amount` compared with contract % plus active Preferred/VB/AGX/Accelerator for that date | Flag any difference over 0.1 pp |
| L3 | Commission on no-shows or waived cancellations | OTA status `ok` but PMS shows no-show or cancelled; or fee waived but commission billed | `amount × pct` ([otasystems](https://otasystems.ai/resources/expedia-commission-recovery)) |
| L4 | Early departure or stay change not reported | PMS nights < OTA nights | `nights_lost × nightly × pct` |
| L5 | Wrong rate or amount (price changed after booking) | OTA price ≠ PMS folio | `(invoiced − actual) × pct` |
| L6 | Unauthorized or stacked discounts | `genius_rate` + mobile + country, or Agoda promotion stack, deeper than the owner's cap | `1 − sell/public > cap` |
| L7 | Rate parity breach | OTA sell price below brand.com or other OTAs for the same stay date and room | Snapshot comparison; flag if diff > 1% |
| L8 | Uncollected virtual card | VCC or EVC balance > 0 after activation | Days to expiry: Booking.com check-out + 365, Expedia + 180, Agoda UPC 24 hours |
| L9 | Expired virtual card | Expiry date passed with balance > 0 | Loss = `current_balance` |
| L10 | Payout shortfall | Payout line ≠ `total_amount_paid − commission − charges`, or bank credit ≠ payout | Match by `bank_transfer_reference` or payout ID |
| L11 | Currency conversion loss | Payout currency ≠ AED | `expected_AED − credited_AED − disclosed_fees`. The AED is pegged at 3.6725 per USD (UNVERIFIED here) |
| L12 | Refund mismatch | OTA refund or negative adjustment with no matching PMS refund, or the reverse | Agoda "Manual adjustments"; Expedia refunds queue |
| L13 | Chargebacks and disputes | Agoda status `Disputed`; Booking.com and Expedia handling UNVERIFIED | Aging report |
| L14 | Missed dispute deadline | Booking.com: 48 hours after check-out. Expedia: 4th of the following month | Countdown alerts |
| L15 | VAT on commission not recovered | Foreign OTA invoice → 5% UAE reverse charge (VAT return Box 3) | `commission × 5%` ([Lexology](https://www.lexology.com/library/detail.aspx?g=243c6cbe-0bb6-4b77-9952-95b5eb297e43), [vatregistrationuae](https://vatregistrationuae.com/vat-on-the-uae-hospitality-sector/)) |

**Implementation keys:**
- Normalize every record to one schema: `{ota, ota_res_id, status, checkin, checkout, nights, gross, commissionable_base, comm_pct, comm_amt, payment_model(OTA_COLLECT|HOTEL_COLLECT), payout_id, payout_amt, payout_ccy, vcc_activation, vcc_expiry, vcc_balance, promo_flags[]}`. The money fields should be integer minor units plus a currency code.
- Match OTA reservations to PMS stays with the key `ota_res_id`, falling back to `(guest_name ~fuzzy, checkin, room_nights)`.

---

## 5. UAE context and commission ranges

- **Typical commission in UAE/GCC** (practitioner benchmarks, not rate cards):
  - Booking.com 15–18%
  - Expedia 18–25% for non-chain hotels
  - Agoda 15–20%
  - Blended rate for Dubai independents about 22%
  - ([Percee Digital](https://www.perceedigital.com/insights/hospitality-direct-bookings/), agency client data)
  - Global ranges for comparison: Booking.com 10–25%, Expedia 15–30% ([Cloudbeds](https://www.cloudbeds.com/online-travel-agencies/commissions/), [otasystems](https://otasystems.ai/resources/expedia-commission-recovery))
  - Add programme uplifts on top: Preferred about +3%, Visibility Booster 5–30% set by the hotel, AGX and Accelerator bids.
- **Dubai guest charges:**
  - 10% service charge, 7% municipality fee and 5% VAT.
  - Tourism Dirham of AED 7–20 per room per night, capped at 30 nights. It is shown as a separate line, and neither the service charge nor the municipality fee is applied to it.
  - ([mazeed](https://mazeed.com/blog/hotel-tax-in-dubai/), [bayut](https://www.bayut.com/mybayut/tourism-dirham-dubai/), secondary sources; the official DET schedule was not fetched, so treat the hotel-apartment tiers as UNVERIFIED)
- **Commission on these charges:** Agoda's reference sell rate is tax-inclusive, so its commission lands on taxes. Booking.com excludes city tax but includes VAT in most countries. For Expedia this is UNVERIFIED. All three make L1 the highest-value check for Dubai.
