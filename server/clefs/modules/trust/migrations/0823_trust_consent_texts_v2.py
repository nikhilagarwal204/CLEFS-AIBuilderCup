"""0823_trust_consent_texts_v2: the platform default consent wording at
version clefs-2026-10b (clefs.modules.trust.consent_texts): the transfer
wording names this deployment (Google Cloud's Gemini models, storage in
Google Cloud's India region) instead of the UAE and Singapore, and Japanese
is added. The clefs-2026-10 platform rows stay for the consents that point at
them, retired so core.consent.current_consent_text serves the new wording."""

from clefs.modules.trust.consent_texts import PREVIOUS_VERSIONS, seed_consent_texts


def up(cur):
    seed_consent_texts(cur)
    cur.execute(
        "UPDATE consent_texts SET retired_at = NOW() "
        "WHERE hotel_slug IS NULL AND version = ANY(%s) AND retired_at IS NULL",
        (list(PREVIOUS_VERSIONS),),
    )
