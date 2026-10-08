"""
0002_core_seed_legacy_hotels: the HOTELS_DATA upsert init_db used to run on
every cold start.

Same SQL as before, so on production it rewrites each row with the values it
already has. knowledge_base is COALESCEd and assistant_name is never touched, so
content an operator set directly in the DB survives (as it did before).
"""

from clefs.legacy.seed_data import HOTELS_DATA

UPSERT_SQL = """
    INSERT INTO hotels (slug, name, star_rating, rooms_count, category, website, city, knowledge_base)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (slug) DO UPDATE SET
        name = EXCLUDED.name,
        star_rating = EXCLUDED.star_rating,
        rooms_count = EXCLUDED.rooms_count,
        category = EXCLUDED.category,
        website = EXCLUDED.website,
        city = EXCLUDED.city,
        knowledge_base = COALESCE(EXCLUDED.knowledge_base, hotels.knowledge_base)
"""


def up(cur):
    for row in HOTELS_DATA:
        # Pad row with None if knowledge_base is missing (as init_db did).
        if len(row) < 8:
            row = row + (None,)
        cur.execute(UPSERT_SQL, row)
