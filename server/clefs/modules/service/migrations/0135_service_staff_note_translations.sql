-- 0135_service_staff_note_translations: a line translated for a staff member
-- (the photo check's reason, for example) is cached apart from guest lines,
-- because the two are worded differently: a guest line may be softened, a
-- note to staff is translated plainly (staff_lang.english_to audience).

ALTER TABLE svc_translations DROP CONSTRAINT IF EXISTS svc_translations_purpose_check;
ALTER TABLE svc_translations
    ADD CONSTRAINT svc_translations_purpose_check CHECK (purpose IN ('job', 'guest', 'staff_note'));
