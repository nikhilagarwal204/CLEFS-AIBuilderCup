"""
Shared helpers for the core domain tests.

Commit handlers are process-global. Profit Guard wraps the real OTA_STATEMENT
handler with its detection and auto-verify hooks when the module loads, so a
test that registers a stand-in and then *unregisters* the kind leaves the rest
of the session without Profit Guard's handler (test order decides whether a
later test sees it). Every temporary handler therefore goes through
swap_commit_handler, which puts back exactly what was there before.
"""

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from clefs.core.ingest import kinds


@contextmanager
def swap_commit_handler(kind: str, fn: Callable[..., Any] | None) -> Iterator[None]:
    """Install `fn` (or remove the handler when fn is None) for `kind`, then
    restore the previous handler on exit, whatever happened in between."""
    prev = kinds.commit_handler(kind)
    if fn is None:
        kinds.unregister_commit_handler(kind)
    else:
        kinds.register_commit_handler(kind, fn)
    try:
        yield
    finally:
        if prev is not None:
            kinds.register_commit_handler(kind, prev)
        else:
            kinds.unregister_commit_handler(kind)
