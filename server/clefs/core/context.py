"""
Per-request context (request id, client ip, user agent).

Set by the request-id middleware in app.py and read by audit() so every audit
row carries where it came from without threading Request through helpers.
Starlette copies contextvars into the threadpool that runs sync handlers, so
these are visible there too.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestInfo:
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None


_current: ContextVar[RequestInfo] = ContextVar("clefs_request_info", default=RequestInfo())


def set_request_info(info: RequestInfo) -> object:
    return _current.set(info)


def reset_request_info(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]


def request_info() -> RequestInfo:
    return _current.get()
