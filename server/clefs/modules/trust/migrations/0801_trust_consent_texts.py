"""0801_trust_consent_texts: seed the platform default consent wording
(clefs.modules.trust.consent_texts, version clefs-2026-10, English and Arabic)
into the foundation consent_texts table. Idempotent: core.consent returns an
identical existing row and never rewrites one."""

from clefs.modules.trust.consent_texts import seed_consent_texts


def up(cur):
    seed_consent_texts(cur)
