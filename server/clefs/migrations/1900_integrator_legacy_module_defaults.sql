-- ─── Legacy hotels keep their original product surface ───
--
-- hotel_module_settings has no row for a module until someone saves its
-- settings, and "no row" means enabled (core.hotels.module_enabled). That
-- default is right for a new hotel, which signs up for the owner autopilot,
-- but wrong for the legacy Indian demo hotels (legacy_open_access = TRUE):
-- they were onboarded for service, concierge, guests, upsell and voice only,
-- and without this file every Wave 2 owner module (and its scheduled jobs,
-- about 60 per hotel) would switch on for them implicitly on deploy.
--
-- So each legacy hotel gets an explicit enabled = FALSE row for the owner
-- modules, only where it has no row yet: a hotel whose owner already turned a
-- module on (or saved its settings) keeps that choice. Service, concierge,
-- guests, upsell and voice stay enabled (no row written). New hotels are not
-- touched and keep every module on. An owner can switch any of these back on
-- from settings (set_module_settings(enabled=True)).
--
-- Idempotent: ON CONFLICT DO NOTHING on the (hotel_slug, module) key.

INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings, updated_at)
SELECT h.slug, m.module, FALSE, '{}'::JSONB, NOW()
FROM hotels h
CROSS JOIN (
    VALUES ('profit_guard'), ('demand'), ('amenities'), ('brief'), ('ask'),
           ('inquiries'), ('visibility'), ('finance'), ('reviews'), ('trust')
) AS m (module)
WHERE h.legacy_open_access
ON CONFLICT (hotel_slug, module) DO NOTHING;
