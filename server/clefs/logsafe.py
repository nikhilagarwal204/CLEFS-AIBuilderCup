"""
Log hygiene for credentials that travel in a URL query string.

    from clefs import logsafe
    logsafe.configure(level)   # create_app and the worker: basicConfig + both guards
    logsafe.install()          # idempotent; clefs.core.serpapi runs it at import
    logsafe.redact("https://x/?q=a&api_key=K")  # -> "https://x/?q=a&api_key=***"

WHY this exists: httpx logs every request at INFO on the "httpx" logger with
the full URL ('HTTP Request: GET https://serpapi.com/search.json?...&api_key=
<live key> "HTTP/1.1 200 OK"'). SerpApi (api_key) and Google CrUX / PageSpeed
(key) accept their credential only in the query string, and create_app and
the worker run logging.basicConfig at INFO. Without these guards every billed
SerpApi call would write the live key to Vercel and VM logs and to any drain
behind them, and anyone with log access could spend the global budget and
bypass PUBLIC_SERPAPI_MONTHLY_CAP.

Two guards, so neither alone has to be perfect:
  - install() puts RedactQuerySecrets on the "httpx" logger. Whatever level
    it runs at (a developer's LOG_LEVEL=DEBUG, a CLI script, a test), a
    record's rendered message has known secret query values replaced by
    "***" before any handler sees it.
  - configure() also raises "httpx" and "httpcore" to WARNING, so the API and
    the worker never log request lines at all; that covers a credential under
    a parameter name the filter does not know yet.
"""

from __future__ import annotations

import logging
import re
import threading

# Query parameter names whose values never belong in a log line. Matched only
# as a whole parameter name after "?" or "&", so "monkey=" is left alone.
SECRET_QUERY_KEYS: tuple[str, ...] = (
    "api_key", "apikey", "key", "access_token", "refresh_token", "id_token", "client_secret", "token", "password",
    "secret", "signature", "sig",
)
_SECRET_RE = re.compile(
    r"(?i)([?&](?:" + "|".join(re.escape(k) for k in SECRET_QUERY_KEYS) + r")=)[^&#\s\"'<>]*"
)
# Loggers that print request URLs: httpx at INFO; httpcore only at DEBUG and
# without the query, but quieted with it for the same reason.
QUIET_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")

_lock = threading.Lock()


def redact(text: str) -> str:
    """Replace the value of every SECRET_QUERY_KEYS parameter with ***."""
    return _SECRET_RE.sub(r"\1***", text)


class RedactQuerySecrets(logging.Filter):
    """Renders the record once and, if it carried a secret query value,
    stores the redacted text as the message (args cleared, so no handler can
    re-render the original from msg % args)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # a malformed record is the logging module's problem, not ours
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg = clean
            record.args = None
        return True


def install() -> None:
    """Attach the filter to the httpx logger once per process."""
    logger = logging.getLogger("httpx")
    with _lock:
        if not any(isinstance(f, RedactQuerySecrets) for f in logger.filters):
            logger.addFilter(RedactQuerySecrets())


def configure(level: int | str) -> None:
    """The process logging setup of the API and the worker."""
    logging.basicConfig(level=level)
    install()
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
