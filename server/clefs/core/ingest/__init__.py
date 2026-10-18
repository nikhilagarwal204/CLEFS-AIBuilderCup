"""clefs.core.ingest: files become canonical rows (Canon: core/ingest; 02 spec
2.2; 04 spec D4).

A file stored through the Files contract becomes one import_batches row. It
is sniffed (encoding, delimiter, sheet, header row), its headers are mapped
to the kind's canonical fields (saved template, then the static alias map,
then an AI proposal a person confirms), its rows are staged in import_rows
next to their raw values, and a commit hands them to the handler registered
for the kind. PDFs are extracted by AI with page references and must pass
the printed-total gate. A corrected re-upload supersedes the earlier batch.

Importing the package registers its task, PII table, inbound-email handler
and the core/stays commit handlers (jobs.OPTIONAL_REGISTRANTS) and exposes
`router` (app.CORE_ROUTER_HOOKS). See README.md for every function and route.
"""

from __future__ import annotations

from clefs.core.ingest.kinds import (
    KINDS,
    SCHEMAS,
    commit_handler,
    mark_row_error,
    post_commit_hook_names,
    post_commit_hooks,
    register_commit_handler,
    register_post_commit_hook,
    registered_handlers,
    schema_for,
    unregister_commit_handler,
)
from clefs.core.ingest.aliases import detect_source, load_maps, resolve as resolve_aliases
# The sniff function is exported as sniff_file: binding the name `sniff` here
# would shadow the submodule that `from clefs.core.ingest import sniff` reads.
from clefs.core.ingest.sniff import header_fingerprint, normalize_header, sniff as sniff_file
from clefs.core.ingest.batches import (
    committable_rows,
    confirm_mapping,
    create_batch,
    edit_row,
    get_batch,
    list_batches,
    list_rows,
    process_batch,
    row_counts,
    set_options,
)
from clefs.core.ingest.commit import commit_batch, discard_batch, maybe_auto_commit, override_gate, redact_contacts
from clefs.core.ingest.mapping import find_template, list_templates, save_template, suggest_mapping

# Registrations run on import: the task and sweep, the core/stays commit
# handlers, the inbound-email handler; then the router for CORE_ROUTER_HOOKS.
from clefs.core.ingest import registrations as _registrations  # noqa: E402,F401
from clefs.core.ingest import inbound as _inbound  # noqa: E402,F401
# batch_view is the one Batch serializer (the routes' shape), for modules that
# serve their own import routes over this package.
from clefs.core.ingest.router import batch_view, router  # noqa: E402

__all__ = [
    "KINDS", "SCHEMAS", "batch_view", "commit_batch", "commit_handler", "committable_rows", "confirm_mapping", "create_batch",
    "detect_source", "discard_batch", "edit_row", "find_template", "get_batch", "header_fingerprint",
    "list_batches", "list_rows", "list_templates", "load_maps", "mark_row_error", "maybe_auto_commit",
    "normalize_header",
    "override_gate", "post_commit_hook_names", "post_commit_hooks", "process_batch", "redact_contacts",
    "register_commit_handler", "register_post_commit_hook", "registered_handlers",
    "resolve_aliases", "router", "row_counts", "save_template", "schema_for", "set_options", "sniff_file",
    "suggest_mapping", "unregister_commit_handler",
]
