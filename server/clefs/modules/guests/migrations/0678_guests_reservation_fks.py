"""
0678_guests_reservation_fks: the 04 spec 3.2 foreign keys to core
reservations that 0670 and 0672 left out while core/stays was being built
(review finding spec-gst-reservation-fk-dropped). Core's `reservations`
table now exists (0021), so the plan and point rows point at it.

Added NOT VALID first: new rows are checked from now on, whatever an older
development database holds. VALIDATE then runs in a savepoint; a database
with orphan rows (a dev copy seeded before 0021) keeps the NOT VALID
constraint and logs why, rather than failing the whole migration. No
ON DELETE action: core never deletes a reservation (it cancels it), and the
spec's DDL has none.
"""

import logging

log = logging.getLogger("clefs.migrate")

FKS = (
    ("gst_journey_plans", "gst_journey_plans_reservation_fk"),
    ("gst_contact_points", "gst_contact_points_reservation_fk"),
)


def _exists(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone() is not None


def up(cur):
    if not _exists(cur, "SELECT 1 FROM pg_class WHERE relname = 'reservations' AND relkind = 'r'", ()):
        log.warning("0678: core reservations table missing; guests reservation FKs not added")
        return
    for table, name in FKS:
        if _exists(cur, "SELECT 1 FROM pg_constraint WHERE conname = %s", (name,)):
            continue
        cur.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY (reservation_id) "
            "REFERENCES reservations (id) NOT VALID"
        )
        cur.execute("SAVEPOINT gst_fk_validate")
        try:
            cur.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
            cur.execute("RELEASE SAVEPOINT gst_fk_validate")
        except Exception as exc:  # orphan rows in an old dev database
            cur.execute("ROLLBACK TO SAVEPOINT gst_fk_validate")
            cur.execute("RELEASE SAVEPOINT gst_fk_validate")
            log.warning("0678: %s left NOT VALID (%s)", name, type(exc).__name__)
