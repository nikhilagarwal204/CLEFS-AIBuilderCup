# Menus, orders and upsell (`upsell`)

Owned by lane **F1 Upsell + Reviews** from Wave 1. Canon 9 moved menus, outlets, orders and upsell events here from the concierge design (03 spec 2.4 `get_menu` / `place_order`, 2.5 DDL). Other modules import only `clefs.modules.upsell.api`.

- Migrations 0550-0599 (`0550_upsell_menu.sql`, `0551_upsell_orders.sql`); tables `ups_*`.
- Console routes under `/hotels/{slug}/menu/` (`router.py`); guest page routes under `/public/menu/{slug}` (`router_public.py`).
- Money: `BIGINT` minor units + `CHAR(3)` currency in storage; Decimal strings (`"45.00"`) next to `*_minor` in every API (Canon 2). Hours and funnel days are hotel-local (`clefs.core.time`, Canon 3).

## Files

| File | What |
|---|---|
| `menu.py` | Outlets, items, pairings: models, CRUD, hotel-local availability, the guest menu payload |
| `pricing.py` | Fee rates (core/tax, else `fee_rules`, else settings) and conventions A/B/C in integer minor units |
| `orders.py` | Baskets, quotes, confirm (the 10-minute session gate), the linked F&B request, lifecycle, funnel |
| `importer.py` | Menu import on core/ingest's MENU kind: the MENU commit handler (upserts, per-row errors, strict) and the one-call `import_csv` (dry run, strict) |
| `metrics.py` | `upsell.orders`, `order_revenue`, `average_order_value`, `upsell_revenue`, `upsell_accept_rate` |
| `jobs.py` | `upsell.stuck_orders` (every 15 min) + signal `upsell.order_stuck`; PII registry entries |
| `settings.py` | `UpsellSettings` (`/hotels/{slug}/module-settings/upsell`) |

## Tables

- `ups_outlets(hotel_slug, key, name_en/ar, kind, hours JSONB [{from, to, days_mask}], delivers_to_room, active, sort)`. Empty hours = always open; a window whose `to` is before its `from` runs past midnight and belongs to the day it opened.
- `ups_menu_items(outlet_id, sku, category(_ar), name_en/ar, desc_en/ar, price_minor, currency, image_file_id -> files, available_from/to TIME, days_mask (bit 0 = Monday), dietary TEXT[], alcohol, prep_min, active, sort)`.
- `ups_upsell_pairs(item_id, suggest_item_id, kind pairing|upgrade, reason_en/ar, active, sort)`.
- `ups_quotes(id UUID, session_ref, room, lines JSONB snapshot, lines_hash, price_display, convention, rates, subtotal/service_charge/municipality_fee/vat/total_minor, currency, expires_at, order_id)`.
- `ups_orders(outlet_id, quote_id UNIQUE, session_ref, room, source, request_id, status placed|accepted|preparing|delivered|cancelled|refunded, price_display, fee split *_minor, total_minor, currency, payment room_charge|cash|card|complimentary, paid_at, verified_room, notes, language, cancel_reason)`.
- `ups_order_items(order_id, menu_item_id, name_snapshot, name_ar_snapshot, unit_price_minor, qty, line_total_minor, notes, upsell_event_id)`.
- `ups_upsell_events(kind pairing|upgrade|late_checkout|spa|other, item_id, source_item_id, session_ref, room, order_id, state shown|accepted|paid|fulfilled|refunded, value_minor, currency, source, at)`; one row per (session, item, state).

## Pricing

`price_display` (setting): `inclusive` (default; the menu price is what the guest pays and the fees are split out of it) or `plus_plus` (the menu price is net; SC, MF and VAT are added). Fees are computed once per basket:

- A: `SC = s*R, MF = m*(R+SC), VAT = v*(R+SC+MF)` (Dubai factor 1.23585)
- B: `SC = s*R, MF = m*R, VAT = v*(R+SC+MF)` (1.2285)
- C: each fee on R (1.22)

Inclusive decomposition rounds R, composes the fees and gives the residual to R, so the parts add up to the price (golden values in `tests/modules/upsell/test_pricing.py`). Rates: `fee_source=auto` uses `clefs.core.tax.resolve_profile` (date-effective rules, owner overrides, the hotel's convention), else this module's own read of `fee_rules` / `hotel_tax_overrides` / `hotel_tax_profiles`, else the settings (10 / 7 / 5 %, convention A). SC with no rule is the hotel's own setting; MF, TF and DEST share the MF slot; VAT with no rule is the settings rate. `fee_source=manual` always uses the settings.

## Quote gate and orders

1. `quote` re-prices every line from the DB: 404 `item_not_found`; 409 `item_unavailable` (inactive or out of hours), `alcohol_not_allowed` (unless `alcohol_orders`), `outlet_no_delivery`, `currency_mismatch`, `ordering_disabled`; 429 per room per hour. It stores a snapshot for `quote_ttl_min` (10) and returns up to `max_suggestions` pairings, logged as `shown`.
2. Confirm needs that quote: same room (409 `quote_room_mismatch`), same session when the quote is bound to one (409 `quote_session_mismatch`), not expired (409 `quote_expired`). Prices stay as quoted; availability is checked again. It writes the order and lines, marks suggested items `accepted`, and files ONE F&B request through `service.api.create_request` (type `settings.request_type`, default `Food`; subtype the outlet; the description lists items, notes and the total; unverified rooms are prefixed `[Room not verified: confirm at the door]`). If Service refuses, the order rolls back. A second confirm of the same quote returns the first order with `deduped: true`.
3. Console lifecycle: `placed -> accepted | preparing | delivered | cancelled`, `accepted -> preparing | delivered | cancelled`, `preparing -> delivered | cancelled`, `delivered -> refunded` (else 409 `illegal_transition`). `delivered` moves accepted upsells to `fulfilled`, `refunded` to `refunded`; `/paid` records `paid`.

## `api.py`

Frozen (CONTRACTS section 13):

- `get_menu(cur, hotel_slug, *, outlet=None, language="en") -> [{outlet, section, item_id, name, description, price, currency, tags}]` plus `price_minor, dietary, alcohol, prep_min, image_url, available_now`. Only items orderable now; `[]` for an unknown hotel.
- `quote(cur, hotel_slug, *, room, lines) -> {quote_id, room, lines, price_display, subtotal, service_charge, municipality_fee, vat, taxes[{code service_charge|municipality_fee|vat, label, rate (a fraction: "0.1" = 10%), amount, amount_minor, included}], total, total_minor, currency, expires_at, suggestions[{item_id, for_item_id, kind, name, reason, price, currency}]}`. `lines = [{item_id | menu_item_id, qty=1, notes?}]`; duplicates merge.
- `create_order(cur, hotel_slug, *, room, lines, source) -> order` (below). The room is the session: it needs an unbound quote (from `quote` or the guest page) for the same room and the same basket (items and quantities) within the TTL, else 409 `quote_required`. A session-bound quote is never confirmable here.

Extensions (not frozen):

- `quote_for_session(cur, hotel_slug, *, room, lines, session_ref, language="en", source=None)`: a quote only `session_ref` can confirm (the concierge passes its session id).
- `confirm_order(cur, hotel_slug, *, quote_id, room, source, session_ref=None, notes=None, verified_room=True, language=None) -> order`.
- `record_upsell_event(cur, hotel_slug, *, kind, state="shown", item_id=None, source_item_id=None, session_ref=None, room=None, value_minor=None, source=None) -> id | None`.
- `order_status_for_guest(cur, hotel_slug, order_id, *, room) -> {order_id, status, total, currency, price_display, created_at, status_changed_at, lines[{name, qty}]} | None`.
- `guest_menu(cur, hotel_slug, *, outlet=None, language="en")`: the guest page payload (below).
- `check_room(cur, hotel_slug, room) -> room`: 400 `invalid_room`; 404 `room_not_found` when the hotel keeps a room register.

Order object: `{id, order_id, room, outlet_id, source, status, request_id, quote_id, price_display, subtotal, service_charge, municipality_fee, vat, total, total_minor, currency, payment, paid_at, verified_room, notes, language, cancel_reason, status_changed_at, created_at, lines[{id, item_id, name, name_ar, qty, unit_price, line_total, notes, upsell}]}`, plus `tracking_token` (the Service request's) and `deduped` from a confirm.

## HTTP API: console (`/hotels/{slug}/menu`)

Browser calls go through `/api/clefs`. Errors are `{detail, code}`. Every write is audited as `menu.<entity>.<verb>`.

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET /outlets` | `menu.view` or `menu.orders` | | `[Outlet]` = `{id, key, name_en, name_ar, kind, hours[{from, to, days_mask}], delivers_to_room, active, sort, created_at, updated_at}` |
| `POST /outlets` | `menu.manage` | `{key, name_en, name_ar?, kind?, hours?, delivers_to_room?, active?, sort?}` (key lowercased, `[a-z0-9][a-z0-9_-]{0,39}`; kind `in_room_dining|restaurant|bar|cafe|pool|spa|minibar|other`) | 201 Outlet; 409 `outlet_exists` |
| `PATCH /outlets/{id}` | `menu.manage` | any of the above except key | Outlet; 404 `outlet_not_found` |
| `DELETE /outlets/{id}` | `menu.manage` | | 204 (items and pairings cascade; orders keep their snapshots) |
| `GET /items?outlet_id=` | `menu.view` or `menu.orders` | | `[Item]` = `{id, outlet_id, sku, category, category_ar, name_en, name_ar, desc_en, desc_ar, price, price_minor, currency, image_file_id, available_from "HH:MM"|null, available_to, days_mask, dietary[], alcohol, prep_min, active, sort, created_at, updated_at}` |
| `POST /items` | `menu.manage` | `{outlet_id, name_en, price "45.00", sku?, category?, category_ar?, name_ar?, desc_en?, desc_ar?, image_file_id? (a file uploaded via POST /hotels/{slug}/files), available_from?, available_to? (both or neither), days_mask?, dietary? (vegan, vegetarian, gluten_free, dairy_free, halal, spicy, contains_nuts, contains_pork, contains_shellfish), alcohol?, prep_min?, active?, sort?}` | 201 Item; 400 `invalid_price` / `invalid_hours`; 404 `file_not_found` / `outlet_not_found`; 422 bad dietary tag |
| `PATCH /items/{id}` | `menu.manage` | any item field (`image_file_id: null` clears the image) | Item |
| `DELETE /items/{id}` | `menu.manage` | | 204 |
| `GET /pairs` | `menu.view` | | `[{id, item_id, suggest_item_id, kind, reason_en, reason_ar, active, sort}]` |
| `POST /pairs` | `menu.manage` | `{item_id, suggest_item_id, kind? pairing|upgrade, reason_en?, reason_ar?, active?, sort?}` (upsert on the pair) | 201 pair; 400 `invalid_pair` |
| `DELETE /pairs/{id}` | `menu.manage` | | 204 |
| `POST /import` | `menu.manage` | `{csv? (text) | file_id?, default_outlet?, create_outlets=true, dry_run=false, strict=false}` | `{rows, created, updated, errors[{row, error}], outlets_created[], dry_run, applied, batch_id, duplicates, already_imported, mapping_required[]}`. The file becomes a MENU import batch (see below) |
| `GET /preview?lang=&outlet=` | `menu.view` | | the guest menu payload (below) as a guest sees it now |
| `POST /quote` | `menu.orders` | `{room, lines[{item_id, qty, notes?}], language?}` | Quote, bound to the calling staff member (a phone order to the desk) |
| `POST /orders` | `menu.orders` | `{quote_id, room, notes?, payment? room_charge|cash|card|complimentary}` | 201 Order (`source: "console"`); 409 `quote_session_mismatch` for another user's quote |
| `GET /orders?status=a,b&room=&days=7&limit=100` | `menu.view` or `menu.orders` | | `[Order]`, newest first, created in the last `days` hotel-local days |
| `GET /orders/{id}` | `menu.view` or `menu.orders` | | Order |
| `POST /orders/{id}/status` | `menu.orders` | `{status, reason?}` | Order; 409 `illegal_transition` |
| `POST /orders/{id}/paid` | `menu.orders` | `{payment?}` | Order with `paid_at`; 409 for cancelled or refunded |
| `GET /funnel?start=&end=` | `menu.view` | local dates, default the last 30 days, at most a year | `{start, end, currency, states{shown|accepted|paid|fulfilled|refunded: {count, value_minor, value}}, accept_rate (0-100 or null), items[{item_id, name, shown, accepted, paid, fulfilled, refunded, accepted_value_minor, accepted_value, accept_rate}]}` |

Module settings (`GET/PUT /hotels/{slug}/module-settings/upsell`): `price_display`, `fee_source auto|manual`, `service_charge_pct`, `municipality_fee_pct`, `vat_pct`, `convention A|B|C`, `quote_ttl_min` (10), `ordering_enabled`, `allow_unverified_rooms`, `alcohol_orders` (false), `orders_per_room_hour` (6), `quotes_per_room_hour` (60), `max_suggestions` (2), `request_type` (`Food`).

## HTTP API: guest page (`/public/menu/{slug}`)

Public, rate-limited per IP (menu reads 120, writes 60, beacons 120 per 10 minutes) and per room (quotes and orders per hour, from the settings). 404 `menu_unavailable` for an unknown hotel or one that switched the module off. `k` is the room's QR key (`clefs.core.rooms.qr_key`): `key_status` is `valid`, `invalid` (wrong or rotated) or `absent` (legacy QR). Orders from a room without a valid key are `verified_room: false` (staff confirm at the door), or 403 `room_not_verified` when `allow_unverified_rooms` is off.

| Method and path | Request | Response |
|---|---|---|
| `GET /public/menu/{slug}?room=&k=&lang=&outlet=` | | `{outlets[{key, name, kind, hours, delivers_to_room, open_now}], items[{item_id, outlet, section, name, description, price, price_minor, currency, tags, dietary, alcohol, prep_min, image_url, available_now}], pairs{item_id: [{suggest_item_id, kind, reason}]}, currency, price_display, fees{sc, mf, vat, convention, source}, ordering_enabled, room, key_status}`. `image_url` is a 1-hour signed path on the API origin (prefix `/api/clefs`). Alcohol is listed only when `alcohol_orders` is on. |
| `POST /public/menu/{slug}/quote` | `{room, k?, lines[{item_id, qty, notes?}], lang?}` | Quote + `key_status` |
| `POST /public/menu/{slug}/orders` | `{room, k?, quote_id, notes?, lang?}` | 201 `{order_id, status, lines, subtotal, service_charge, municipality_fee, vat, total, currency, price_display, verified_room, created_at, deduped, tracking_token, order_token}`; 409 `quote_*` |
| `GET /public/menu/{slug}/orders/{order_token}` | | `{order_id, status, total, currency, price_display, created_at, status_changed_at, lines[{name, qty}]}`; 404 for a forged or foreign token |
| `POST /public/menu/{slug}/events` | `{room, item_id, source_item_id?, kind? pairing|upgrade}` | 204. Logs `shown` only (a guest cannot claim an acceptance); repeats in one stay day collapse |

## Jobs, signals, metrics

- `upsell.stuck_orders` (every 15 minutes, per hotel): signal `upsell.order_stuck` (service_risk, daily cadence, owner `dept_head`) for each order still placed/accepted/preparing after 90 minutes; resolved when it moves on.
- Metrics (permission `menu.view`; revenue excludes cancelled and refunded orders): `upsell.orders`, `upsell.order_revenue`, `upsell.average_order_value` (dimensions outlet, source, payment), `upsell.upsell_revenue`, `upsell.upsell_accept_rate` (dimension kind; 0-100).
- PII (Trust's retention and DSAR): order and line notes redacted after 730 days; unconfirmed quotes deleted after 30 days.

## Foundation requests

- Menu imports run on `clefs.core.ingest` (kind `MENU`, alias map `core/ingest/aliases/menu.yaml`). `POST /import` stores the file, stages it as a MENU batch, and commits it through `importer.commit_menu` with `{default_outlet, create_outlets, strict}` as commit options.
  - The same bytes are one batch: a second import of the same file writes nothing and returns the earlier result with `already_imported: true`. A dry run leaves its batch in REVIEW, and the real import of the same file commits that batch.
  - Headers the alias map cannot place leave the batch MAPPING_REQUIRED: `mapping_required` lists the fields and nothing is written. The Imports console (`/ingest/batches/{id}/mapping`, with the AI proposal) can map them and commit with the same options.
  - Refused rows are ERROR on the batch, with their reason. `errors[].row` is the file line.
- Order status does not yet follow the linked Service request (a request marked done does not deliver the order). Service would need a hook such as `on_request_state(fn)` in its `api.py`.
