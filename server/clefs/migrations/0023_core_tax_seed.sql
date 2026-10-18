-- 0023_core_tax_seed: the Dubai and Abu Dhabi fee rules and the USD/AED peg
-- (Canon: core/tax; 02 spec 2.3; research/uae/uae-taxes-fees.md, checked
-- 2026-09-27).
--
-- Every row carries its source_url (Canon: UAE defaults). Idempotent through
-- rule_key, so re-running never duplicates and an owner's override rows keep
-- pointing at stable ids.
--
-- effective_from 2000-01-01 is a sentinel for "in force before any data Clefs
-- imports": the exact commencement dates of the Dubai service charge, the 7%
-- municipality fee (Decree 27/2019) and the Tourism Dirham (ECR 2/2014) do not
-- change any figure Clefs computes.
--
-- The 2026 Dubai announcements are ANNOUNCED_UNVERIFIED: core/tax applies them
-- to a hotel only while it has a hotel_tax_overrides row with apply = TRUE,
-- and every stay they intersect carries a notice until someone verifies them.
-- Other emirates stay empty: an owner configures them (verified_by required).

-- ─── Dubai: percentages ───

INSERT INTO fee_rules (rule_key, emirate, property_category, fee_code, calc_mode, rate, currency, base,
                       vat_applies, effective_from, rule_status, label, params, source_url, verified_by, verified_at)
VALUES
    ('dxb_sc_10', 'DXB', '*', 'SC', 'PERCENT', 0.10, 'AED', '{ROOM}', TRUE, '2000-01-01', 'VERIFIED',
     'Service charge 10% (a hotel commercial charge, not a government levy)', '{}',
     'https://www.rovehotels.com/en/offer/marjan-monthly-stays/', 'research:uae-taxes-fees.md', '2026-09-27'),
    ('dxb_mf_7', 'DXB', '*', 'MF', 'PERCENT', 0.07, 'AED', '{ROOM,SC}', TRUE, '2000-01-01', 'VERIFIED',
     'Municipality fee 7% of hotel establishment sales (Decree 27/2019)', '{}',
     'https://dlp.dubai.gov.ae/Legislation%20Reference/2019/Decree%20No.%20(27)%20of%202019%20Concerning%20Reduction%20of%20and%20Exemption%20from%20Fees%20and%20Fines%20in%20the%20Emirate%20of%20Dubai.html',
     'research:uae-taxes-fees.md', '2026-09-27'),
    ('dxb_vat_5', 'DXB', '*', 'VAT', 'PERCENT', 0.05, 'AED', '{ROOM,SC,MF}', TRUE, '2018-01-01', 'VERIFIED',
     'VAT 5% on room, service charge and municipality fee (never on the Tourism Dirham)', '{}',
     'https://taxadvisors.ae/guides/hotel-hospitality-tax-uae/', 'research:uae-taxes-fees.md', '2026-09-27')
ON CONFLICT (rule_key) DO NOTHING;

-- ─── Dubai: Tourism Dirham (ECR 2/2014 Schedule 1; rules AR 2/2020) ───
-- Per room-night of the main guest, per bedroom for multi-bedroom units,
-- capped at 30 consecutive nights, outside VAT, only on stayed nights.

INSERT INTO fee_rules (rule_key, emirate, property_category, fee_code, calc_mode, rate, currency, base,
                       vat_applies, cap_consecutive_nights, per_bedroom, effective_from, rule_status, label,
                       params, source_url, verified_by, verified_at)
SELECT 'dxb_td_' || t.cat, 'DXB', t.cat, 'TD', 'PER_ROOM_NIGHT', t.aed, 'AED', '{}', FALSE, 30, TRUE,
       '2000-01-01', 'VERIFIED', 'Tourism Dirham fee (' || t.name || ')',
       jsonb_build_object('rules_url',
           'https://dlp.dubai.gov.ae/Legislation%20Reference/2020/Administrative%20Resolution%20No.%20(2)%20of%202020%20Prescribing%20the%20Rules%20for%20Calculating,%20Collecting,%20and%20Paying%20the%20Tourism%20Dirham%20Fee%20in%20the%20Emirate%20of%20Dubai.html'),
       'https://dlp.dubai.gov.ae/Legislation%20Reference/2014/Executive%20Council%20Resolution%20No.%20(2)%20of%202014.html',
       'research:uae-taxes-fees.md', TIMESTAMPTZ '2026-09-27'
FROM (VALUES
    ('hotel_5', 20, '5-star or higher hotel/resort'),
    ('hotel_4', 15, '4-star hotel/resort'),
    ('hotel_3', 10, '3-star hotel/resort'),
    ('hotel_2', 10, '2-star hotel'),
    ('hotel_1', 7, '1-star or budget hotel'),
    ('apartment_luxury', 20, 'luxury hotel apartment'),
    ('apartment_deluxe', 15, 'deluxe hotel apartment'),
    ('apartment_standard', 10, 'standard hotel apartment'),
    ('guest_house', 7, 'guest house'),
    ('holiday_home_luxury', 15, 'luxury holiday home'),
    ('holiday_home_standard', 10, 'standard holiday home')
) AS t (cat, aed, name)
ON CONFLICT (rule_key) DO NOTHING;

-- ─── Dubai: 2026 announcements (ANNOUNCED_UNVERIFIED) ───
-- 1 Apr 2026: a 3-month deferral of paying the sales fees and the Tourism
-- Dirham (a remittance deferral, not a waiver). 21 May 2026: "exemption from
-- collection of the Tourism Dirham and the sales fees"; the effective and end
-- dates were left to the authorities, so the exemption rows are open-ended and
-- an owner narrows them in hotel_tax_overrides to the confirmed dates.

INSERT INTO fee_rules (rule_key, emirate, property_category, fee_code, calc_mode, rate, currency, base,
                       vat_applies, effective_from, effective_to, rule_status, label, params, source_url)
VALUES
    ('dxb_2026_deferral_td', 'DXB', '*', 'TD', 'REMIT_DEFERRAL', 0, 'AED', '{}', FALSE, '2026-04-01', '2026-06-30',
     'ANNOUNCED_UNVERIFIED', 'Dubai 2026: Tourism Dirham remittance deferred 3 months (dates unverified)',
     '{"remit_deferral_months": 3, "group": "dxb_2026_deferral"}',
     'https://mediaoffice.ae/en/news/2026/april/02-04/dubai-economic-measures-boost-hospitality-and-business-growth'),
    ('dxb_2026_deferral_mf', 'DXB', '*', 'MF', 'REMIT_DEFERRAL', 0, 'AED', '{}', FALSE, '2026-04-01', '2026-06-30',
     'ANNOUNCED_UNVERIFIED', 'Dubai 2026: municipality (sales) fee remittance deferred 3 months (dates unverified)',
     '{"remit_deferral_months": 3, "group": "dxb_2026_deferral"}',
     'https://mediaoffice.ae/en/news/2026/april/02-04/dubai-economic-measures-boost-hospitality-and-business-growth'),
    ('dxb_2026_exempt_td', 'DXB', '*', 'TD', 'PER_ROOM_NIGHT', 0, 'AED', '{}', FALSE, '2026-05-21', NULL,
     'ANNOUNCED_UNVERIFIED', 'Dubai 2026: Tourism Dirham not collected (exemption; dates unverified)',
     '{"group": "dxb_2026_exemption", "exemption": true}',
     'https://mediaoffice.ae/en/news/2026/may/21-05/hamdan-bin-mohammed'),
    ('dxb_2026_exempt_mf', 'DXB', '*', 'MF', 'PERCENT', 0, 'AED', '{ROOM,SC}', TRUE, '2026-05-21', NULL,
     'ANNOUNCED_UNVERIFIED', 'Dubai 2026: municipality (sales) fee not collected (exemption; dates unverified)',
     '{"group": "dxb_2026_exemption", "exemption": true}',
     'https://mediaoffice.ae/en/news/2026/may/21-05/hamdan-bin-mohammed')
ON CONFLICT (rule_key) DO NOTHING;

-- ─── Abu Dhabi (since 1 Sep 2023) ───
-- Tourism fee cut from 6% to 4% and the AED 15 per room-night fee removed on
-- 1 Sep 2023. The published base is "the value of the invoice"; [ROOM, SC]
-- matches the compound convention (A factor 1.2474) and B/C override it.
-- The service charge is customary, not a levy: it is OWNER_CONFIGURED.

INSERT INTO fee_rules (rule_key, emirate, property_category, fee_code, calc_mode, rate, currency, base,
                       vat_applies, effective_from, rule_status, label, params, source_url, verified_by, verified_at)
VALUES
    ('auh_tf_4', 'AUH', '*', 'TF', 'PERCENT', 0.04, 'AED', '{ROOM,SC}', TRUE, '2023-09-01', 'VERIFIED',
     'Tourism fee 4% (DCT Abu Dhabi, from 1 Sep 2023)', '{"base_unverified": true}',
     'https://www.mediaoffice.abudhabi/en/tourism/department-of-culture-and-tourism-abu-dhabi-revises-hotel-fees-to-foster-investment-in-hospitality-sector/',
     'research:uae-taxes-fees.md', '2026-09-27'),
    ('auh_mf_4', 'AUH', '*', 'MF', 'PERCENT', 0.04, 'AED', '{ROOM,SC}', TRUE, '2023-09-01', 'VERIFIED',
     'Municipality fee 4% of the invoice (Abu Dhabi, from 1 Sep 2023)', '{"base_unverified": true}',
     'https://www.mediaoffice.abudhabi/en/tourism/department-of-culture-and-tourism-abu-dhabi-revises-hotel-fees-to-foster-investment-in-hospitality-sector/',
     'research:uae-taxes-fees.md', '2026-09-27'),
    ('auh_vat_5', 'AUH', '*', 'VAT', 'PERCENT', 0.05, 'AED', '{ROOM,SC,MF,TF}', TRUE, '2018-01-01', 'VERIFIED',
     'VAT 5% on room, service charge, municipality and tourism fees', '{}',
     'https://taxadvisors.ae/guides/hotel-hospitality-tax-uae/', 'research:uae-taxes-fees.md', '2026-09-27')
ON CONFLICT (rule_key) DO NOTHING;

-- ─── FX: the USD/AED peg ───
-- AED 3.6725 per USD since November 1997 (Central Bank of the UAE).
-- clefs.core.money reads the latest row on or before a date in either
-- direction, so this one row serves every date after it.

INSERT INTO fx_rates (date, base, quote, rate, source, source_url)
VALUES ('1997-11-01', 'USD', 'AED', 3.6725, 'PEG', 'https://www.centralbank.ae/en/')
ON CONFLICT (date, base, quote) DO NOTHING;
