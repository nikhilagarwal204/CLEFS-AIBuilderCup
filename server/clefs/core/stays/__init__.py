"""clefs.core.stays: what the PMS says happened (Canon: core/stays; 02 spec
2.1; 04 spec D1-D3).

Reservations and their nights, folio charges, occupancy (daily and by
floor), on-the-books snapshots, baseline exclusions, OTA-claim matching,
sealed reservation contacts and the stay-event outbox. PMS data is truth and
OTA data a claim: the two meet only through reservation_matches.

Importing the package registers its jobs, metrics and PII tables
(jobs.OPTIONAL_REGISTRANTS) and exposes `router` (app.CORE_ROUTER_HOOKS).
See README.md for every function, route, job and event.
"""

from __future__ import annotations

from clefs.core.stays.contacts import (
    OTA_WINDOW_DAYS,
    PMS_DIRECT_RETENTION_DAYS,
    classify as classify_contact,
    contact_value,
    contacts_for,
    purge_contacts,
    refresh_lifetimes,
    reservations_for_contact,
    set_contacts,
)
from clefs.core.stays.events import (
    dispatch,
    emit,
    events_for,
    on_stay_event,
    registered_consumers,
    replay_failure,
    unregister_consumer,
)
from clefs.core.stays.nights import refresh_stayed, stayed_flag
from clefs.core.stays.normalize import name_similarity, normalize_name, normalize_ota_res_id
from clefs.core.stays.reservations import (
    ensure_channel,
    find_reservation,
    get_reservation,
    rebuild_nights,
    relink_chain,
    upsert_reservation,
)

# Registrations (jobs, PII tables, metrics) run on import.
from clefs.core.stays import jobs as _jobs  # noqa: E402,F401
from clefs.core.stays import registrations as _registrations  # noqa: E402,F401
from clefs.core.stays.matching import (  # noqa: E402
    AUTO_LINK,
    REVIEW,
    Claim,
    confirm_match,
    delete_matches,
    get_match,
    match_claim,
    match_for_line,
    rematch_open,
    reject_match,
    review_queue,
)
from clefs.core.stays.attribution import apply_attribution, attributed_bookings  # noqa: E402
from clefs.core.stays.breakdown import reservation_breakdown, stay_lines  # noqa: E402
from clefs.core.stays.folio import folio_for, folio_totals, link_orphans, upsert_folio_charges  # noqa: E402
from clefs.core.stays.occupancy import (  # noqa: E402
    add_exclusion,
    baseline_exclusions,
    clear_override,
    derive_occupancy,
    is_excluded,
    get_occupancy,
    occupancy_by_floor,
    remove_exclusion,
    set_occupancy,
)
from clefs.core.stays.otb import get_otb, latest_snapshot_date, pace, snapshot_otb  # noqa: E402
from clefs.core.stays.router import router  # noqa: E402
