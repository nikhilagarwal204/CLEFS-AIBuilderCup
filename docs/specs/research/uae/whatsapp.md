> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

## 1. Cloud API setup and version

- **Graph API version:** Meta's current doc examples use **v25.0** (`https://graph.facebook.com/v25.0/...`) ([reply buttons](https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages)). **v26.0 was released on 2026-07-29**, and some of its breaking changes apply to every version from 2026-10-27 ([ppc.land](https://ppc.land/meta-blocks-47-commerce-endpoints-as-graph-api-v26-0-lands-today/)). Keep the version in an env var such as `WA_GRAPH_VERSION=v25.0`.
- **Setup steps** ([get started](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started)):
  1. Create a Meta app using the "Connect with customers through WhatsApp" use case.
  2. Link a business portfolio.
  3. Generate a temporary token for testing.
  4. For production, create a System User and give its token the permissions `business_management`, `whatsapp_business_messaging` and `whatsapp_business_management`.
- **Test number:** it can only message **up to 5** pre-approved recipient numbers ([WANotifier](https://help.wanotifier.com/en/article/test-phone-number-limitations-in-direct-setup-kt0ly2/), [respond.io](https://respond.io/help/whatsapp/whatsapp-cloud-api)). Not confirmed on a Meta page.
- **Send endpoint:** `POST /{PHONE_NUMBER_ID}/messages` with headers `Authorization: Bearer <token>` and `Content-Type: application/json`. The response contains `messages[0].id` (store it as `wamid`) and `contacts[0].wa_id` ([send messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)).
- **Onboarding hotels (multi-tenant):** Embedded Signup returns the hotel's WABA ID, its phone number ID, and a code you exchange for a token.
  - As a Tech Provider, each hotel adds its own payment method.
  - The default limit is 10 new customers per rolling 7 days. It rises to 200 after Business Verification, App Review and Access Verification ([Embedded Signup](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview)).
  - Suggested split: send the owner brief from one Clefs number, and use each hotel's own number for guest messaging.

## 2. Payloads

### 2a. Template with quick-reply buttons (the 7am brief, sent outside the 24h window)

To create it: `POST /{WABA_ID}/message_templates` with `name` (lowercase letters and underscores), `language`, `category: "utility"`, `parameter_format: "named"` and `components` ([utility templates](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/utility-templates/utility-templates)).

```json
{"name":"owner_daily_brief","language":"en_US","category":"utility","parameter_format":"named",
 "components":[
  {"type":"header","format":"text","text":"Daily brief: {{hotel_name}}"},
  {"type":"body","text":"Occupancy yesterday {{occ}}, ADR AED {{adr}}. {{n_actions}} items need your decision today.",
   "example":{"body_text_named_params":[{"param_name":"occ","example":"82%"},{"param_name":"adr","example":"410"},{"param_name":"n_actions","example":"3"}]}},
  {"type":"footer","text":"Reply STOP to pause briefs"},
  {"type":"buttons","buttons":[{"type":"quick_reply","text":"Approve all"},{"type":"quick_reply","text":"Review items"},{"type":"quick_reply","text":"Call me later"}]}]}
```
(`hotel_name` also needs a header example.)

**Template limits** ([components](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/components)):
- Header text: 60 characters. Body: 1,024. Footer: 60. Quick-reply button text: 25.
- Up to 10 buttons in total (all 10 may be quick replies). At most 2 URL buttons and 1 phone button.
- A variable cannot start or end the body, and two variables cannot sit next to each other.
- `format: "DOCUMENT"` gives you a PDF header.

**Quotas** ([templates](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates)):
- 100 template creations per hour per WABA.
- 250 templates if the business is unverified, up to 6,000 once verified.
- Review takes up to 24 hours.

**Sending it:**
```json
{"messaging_product":"whatsapp","to":"9715XXXXXXXX","type":"template",
 "template":{"name":"owner_daily_brief","language":{"code":"en_US"},
  "components":[
   {"type":"body","parameters":[{"type":"text","parameter_name":"occ","text":"82%"}]},
   {"type":"button","sub_type":"quick_reply","index":"0","parameters":[{"type":"payload","payload":"brief:2026-09-27:approve_all"}]}]}}
```
The button structure (`type: button`, `sub_type: quick_reply`, `index`, `parameters[{type: payload}]`) is from the [components](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/components) page. The maximum payload length is **UNVERIFIED**.

**Webhook when a quick reply is tapped** ([button webhook](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/button)):
```json
{"context":{"from":"<biz number>","id":"<template wamid>"},"from":"9715...","id":"wamid...","timestamp":"...",
 "type":"button","button":{"payload":"brief:2026-09-27:approve_all","text":"Approve all"}}
```
Match `context.id` against the stored `wamid` so you know which brief was answered. When no payload is set at send time, `payload` just repeats the button label.

### 2b. Interactive reply buttons (only inside the 24h window)

`type: "interactive"`, `interactive.type: "button"` ([reply buttons](https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages)):
- **Max 3 buttons.** Button `id` up to 256 characters. Button `title` up to **20** characters and must be unique.
- Body up to 1,024 characters, footer up to 60.
- The webhook arrives as `messages[].type="interactive"` with `interactive.type="button_reply"`, and gives you `interactive.button_reply.id` and `.title`.

### 2c. Interactive list (only inside the 24h window)

([list messages](https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages)):
- Up to **10 sections** but only **10 rows in total** across them, and a single button.
- Character limits: row `title` 24, row `description` 72, row `id` 200, section title 24, button text 20, body 4,096, header 60, footer 60.
- The webhook arrives as `interactive.type="list_reply"` with `list_reply.id`, `.title` and `.description`.
- Use it for "Review items", with one row per pending approval.

### 2d. Documents (PDF)

- The `document` object takes either `id` or `link`, plus optional `caption` and `filename`. Maximum size is 100 MB. Supported types include `application/pdf`, xlsx, docx and pptx ([document messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/document-messages)).
- To upload: `POST /{PHONE_NUMBER_ID}/media` as multipart with the fields `file`, `type` and `messaging_product=whatsapp`, which returns an `id` ([media](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media)).
  - Media IDs from uploads expire after **30 days**. Media IDs in webhooks expire after **7 days**.
  - `GET /{MEDIA_ID}` gives a download URL that is only valid for **5 minutes**.
- To send a PDF outside the 24h window, use a template with a `DOCUMENT` header.

### 2e. Webhooks

([create endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint)):
- **Verification (GET):** Meta sends `hub.mode=subscribe`, `hub.verify_token` and `hub.challenge`. Return 200 with the challenge.
- **Events (POST):** check the `X-Hub-Signature-256` header, which is `sha256=` followed by an HMAC-SHA256 of the raw body keyed with the app secret. Self-signed TLS certificates are not accepted.
- Batches hold up to 1,000 updates. Failed deliveries are retried for up to 7 days, and batching is not guaranteed, so **deduplicate on `messages[].id`**.
- The status values `sent`, `delivered`, `read` and `failed`, and the `pricing` object, are **UNVERIFIED** (the page I fetched does not cover them).

## 3. Categories, the 24h window and pricing

**How templates are categorised** ([categorization](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization)):
- A **utility** template must be non-promotional **and** either specific to the user or requested by the user.
- Mixed or unclear content is classed as **marketing** by default.
- Meta can recategorise a utility template automatically. It stays `APPROVED` but is billed as marketing. You can appeal within 60 days.
- Meta's own example of a valid utility message is a daily account summary: "Daily update for account ending in {{four_digit_number}}: Your available balance is {{amount}}."
- **To keep the owner brief classed as utility:** stick to the hotel's own KPIs, alerts and pending decisions. Never include upsells, "try our new feature" or offers. Keep feature announcements in a separate marketing template or in email.

**24h customer service window (CSW):** it opens whenever the user messages you, and that includes tapping a quick reply. While it is open you can send any message type. Once it closes, only templates can be sent ([pricing](https://developers.facebook.com/docs/whatsapp/pricing)).

**Pricing model** ([pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)):
- Since 2025-07-01 Meta bills per **delivered template message**, by category and by the recipient's country code.
- Utility and authentication templates get volume tiers, aggregated per business portfolio and reset monthly.
- A 72h free entry point applies after a Click-to-WhatsApp ad or Page CTA.
- Meta updates rates on 1 January, 1 April, 1 July and 1 October.

**IMPORTANT, starting in 4 days:** from **2026-10-01**, Meta starts charging per message for **service (non-template) messages and for utility templates sent inside the CSW**, at the market's utility rate ([Meta non-template pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages)).
- 360dialog reports a free allowance of **1,000 service messages per business phone number per month**, with no rollover. The Meta page I fetched did not mention that allowance, so treat it as partly **UNVERIFIED**.
- 360dialog also reports that directly integrated businesses **with no payment method on file by 2026-09-30 will stop having service messages delivered** ([360dialog](https://360dialog.com/blog/whatsapp-service-message-charging-october-2026/)).
- Meta Business Agent: $2.00 per 1M tokens from 2026-08-01 ([Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages)).

**Rates.** Meta publishes exact rates only in its rate card CSV/PDF, so treat all of these as indicative:

| Market | Marketing | Utility | Authentication | Source |
|---|---|---|---|---|
| UAE (USD) | about $0.05 (Meta confirms a marketing increase on 2025-10-01; the older figure was $0.0385) | about $0.015–0.016 | about $0.016–0.020 | [adjoltz](https://adjoltz.com/uae-whatsapp-rate-card), [ominiflow](https://ominiflow.com/whatsapp-api-pricing/uae), [tawasel](https://tawasel.io/blog/whatsapp-business-api-pricing-uae.html) |
| India (INR, before 18% GST) | ₹0.8631 | ₹0.115 | ₹0.115 | [whautomate](https://whautomate.com/whatsapp-business-api-pricing-india), [myoperator](https://myoperator.com/blog/whatsapp-business-api-pricing-india-2026) |

India also moved to INR billing on 2026-01-01, and every Indian WABA must move to INR by 2026-12-31 ([Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)).

**Worked estimate:** 30 hotels × 2 recipients × 30 days = 1,800 utility templates × about $0.016 ≈ **$29 a month**, plus follow-ups inside the CSW once they become chargeable.

## 4. Verification, display name and limits

**Business verification:**
- Needs a valid UAE trade licence. The legal name must match across the licence, the website and Business Manager.
- Put the legal name and address in the website footer, privacy policy and terms, and verify the domain.
- Review takes about 3–10 business days ([360dialog docs](https://docs.360dialog.com/docs/resources/meta-business-verification), [zaple](https://zaple.ai/blog/meta-business-verification-whatsapp/)). These are secondary sources.

**Display name** ([display names](https://developers.facebook.com/documentation/business-messaging/whatsapp/display-names)):
- `name_status` is one of `APPROVED`, `DECLINED` or `PENDING_REVIEW`.
- You can change the name up to 10 times per 30 days. After approval you must re-register the number within 14 days.
- The full naming rules are in the [Help Center](https://www.facebook.com/business/help/757569725593362) (**UNVERIFIED**, not fetched).

**Messaging limits** ([messaging limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits)):
- Tiers are 250, then 2K, 10K, 100K and unlimited unique users per rolling 24h. The limit applies to the **whole business portfolio** and counts only business-initiated messages sent outside the CSW.
- You reach 2K by getting verified, or by sending 2K high-quality templates within 30 days.
- After that, increases are automatic when quality is good and you use at least half your limit over 7 days.

**Throughput** ([throughput](https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput)):
- 80 messages per second per number by default, rising to 1,000. Going over returns error **130429**.
- **Pair rate limit:** 1 message every 6 seconds to the same user, otherwise error **131056** ([Heltar](https://www.heltar.com/blogs/how-to-resolve-whatsapp-business-api-error-code-131056-pair-rate-limit-hit-cm3kmbksu000xvnqr4ql87km1)). This is secondary. Queue staff alerts per recipient because of it.

**Opt-in** ([opt-in](https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in)):
- The opt-in must name the business and state that messages will come on WhatsApp.
- Meta suggests a separate opt-in per category, and you must honour opt-outs.
- Store `opted_in_at`, `method` and `categories` for each contact.

## 5. UAE specifics

- **Messaging works, calls do not.** WhatsApp text messaging works normally. Voice and video calls are blocked by TDRA's VoIP policy; only licensed apps such as Botim or C'Me are allowed ([simology](https://simology.io/blog/uae-voip-app-calling-what-works-what-doesnt-2026-traveller-guide), [Gulf News](https://gulfnews.com/amp/story/business%2Fwhatsapp-video-voice-calls-blocked-again-1.2047642)). Do not build any "call via WhatsApp" features.
- **Business Calling API:** respond.io lists it as "Restricted or unavailable … United Arab Emirates (UAE), Saudi Arabia, Qatar, and Egypt" ([respond.io](https://respond.io/help/whatsapp/whatsapp-business-calling-api)). Meta's own page only lists US, CA, EG, VN and NG as exclusions, so the UAE status is **UNVERIFIED** on Meta's side.
- **Telemarketing rules:** Cabinet Decision No. 56 of 2024 (effective 2024-08-27) reportedly covers marketing via messaging apps "including WhatsApp". It requires prior consent, a clear business identity, contact only between **9:00 and 18:00**, and respect for TDRA's Do-Not-Call registry. Penalties are in Decision No. 57 of 2024 ([adjoltz](https://adjoltz.com/uae-whatsapp-rate-card)). I did not check the primary text (**UNVERIFIED**).
  - A utility 7am brief to consenting owners should not count as marketing. Still, apply the 9–18 window to any **marketing** sends to guests.
- **Data protection:** Federal Decree-Law 45 of 2021 (PDPL) applies. DIFC and ADGM have their own regimes (same source).
- **Timezone:** UAE is UTC+4 with no daylight saving, so 7:00 GST is **03:00 UTC**.

## 6. Alternative providers

| Provider | Model | Pros | Cons |
|---|---|---|---|
| Meta Cloud API (direct) | Meta fees only | Cheapest, newest features, pinned version | You run webhooks, verification and billing yourself; a payment method is needed by 2026-09-30 |
| Twilio | +$0.005 per message sent or received, +$0.001 per failed message, plus Meta fees ([Twilio](https://www.twilio.com/en-us/whatsapp/pricing)) | One API for WhatsApp, SMS and email (SendGrid); good SDKs | Markup on every message, including inbound |
| 360dialog | €49, €99 or €500 per number per month, **no markup** on Meta fees; partner plans from €250/month ([360dialog](https://360dialog.com/pricing)) | Suits an ISV hosting many hotels | Monthly fee per number |
| Gupshup | Plan of about $80 plus about $0.001 per message ([zoko](https://www.zoko.io/post/whatsapp-api-gupshup-pricing-features)) | Strong in India and MENA | Layered pricing (**UNVERIFIED**) |
| Infobip | Quote only; ISO 27001, regional data centres ([Infobip](https://www.infobip.com/blog/best-whatsapp-api)) | Enterprise, omnichannel, also does SMS | Expensive for SMB; UAE office **UNVERIFIED** |

All four are listed as active in the UAE ([ycloud list](https://www.ycloud.com/blog/top-whatsapp-business-api-solution-providers-dubai)). **Suggestion:** build a `MessagingProvider` interface (`sendTemplate`, `sendInteractive`, `sendDocument`, `parseWebhook`), ship a Meta Cloud adapter first, and keep a Twilio adapter as the fallback path.

## 7. Fallbacks when WhatsApp is not configured or fails

**Order:** WhatsApp template → email (always available) → SMS for critical alerts only.

Trigger the fallback when:
- there is no `wa_phone_number_id` for the hotel,
- the recipient has not opted in,
- the send returns 131056 or 130429 after retries, or
- a `failed` status arrives (**UNVERIFIED**; see 2e).

**UAE SMS rules:**
- An alphanumeric sender ID must be pre-registered **separately with e& and du**. Documents needed:
  - an NOC on company letterhead, stamped,
  - the carrier's sender-ID form,
  - the signatory's ID,
  - a trademark certificate if the sender ID does not match the company name.
- Registration takes about 5–10 business days for UAE companies and 20–25 for foreign ones ([Telerivet](https://www.telerivet.com/blog/uae-sms-compliance-tdra-ad-prefix)). Twilio quotes about 2 weeks ([Twilio UAE](https://www.twilio.com/en-us/guidelines/ae/sms)).
- Promotional sender IDs need the **`AD-` prefix**, which counts toward the **11-character** limit. Promotional SMS may only be sent between 07:00 and 21:00.
- **Long codes, short codes and two-way SMS are not supported.** Twilio says "Messages containing URL, WhatsApp/LINE chat links, and phone number in body are also not allowed" ([Twilio UAE](https://www.twilio.com/en-us/guidelines/ae/sms)). So **SMS fallback text cannot carry links, one-tap actions or wa.me links**. Keep it to plain transactional alerts like "Clefs: 2 approvals pending, check email."

## 8. Implementation checklist for Clefs

1. **Tables:**
   - `wa_accounts` (hotel_id, waba_id, phone_number_id, access_token_enc, display_name, name_status, messaging_tier, quality)
   - `wa_templates` (name, language, category, status, last_category_change)
   - `wa_messages` (wamid, hotel_id, recipient, direction, type, template_name, category, status, pricing_category, context_wamid, payload, created_at)
   - `wa_contacts` (phone_e164, role owner|gm|staff|guest, opted_in_at, opt_in_method, categories[], csw_expires_at)
2. **7am brief:**
   - A cron job at 03:00 UTC sends the utility template with 3 quick replies. Encode the action and a nonce in the payload, e.g. `brief:{date}:{action}:{nonce}`. Attach the PDF as a `DOCUMENT` header or send it afterwards.
   - The tap opens the CSW. Follow-ups go out as an interactive list (up to 10 items) or reply buttons (up to 3).
3. **Idempotency:** make webhook handling idempotent on `wamid`, verify the HMAC signature, and deduplicate button taps using `context.id` together with the payload.
4. **Per-recipient queue:** at least a 6-second gap per recipient, a global cap below 80 messages per second, and exponential backoff on 130429 and 131056.
5. **Billing:** track the monthly count of service messages per number against the reported 1,000 free, and make sure a Meta payment method is on file **before 2026-09-30**.

