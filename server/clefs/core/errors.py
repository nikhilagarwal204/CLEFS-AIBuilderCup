"""
Error type shared by every module.

Raise ClefsError(status, detail, code) anywhere; app.py turns it into
{"detail": str, "code": str} which keeps the {"detail": ...} shape the legacy
dashboards already read while giving new clients a stable machine code.
"""

from __future__ import annotations


class ClefsError(Exception):
    def __init__(self, status: int, detail: str, code: str | None = None, *, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.code = code
        self.headers = headers

    def to_dict(self) -> dict[str, str]:
        out = {"detail": self.detail}
        if self.code:
            out["code"] = self.code
        return out


def unauthorized(detail: str = "Sign in required", code: str = "unauthorized") -> ClefsError:
    return ClefsError(401, detail, code)


def forbidden(detail: str = "You do not have access to this", code: str = "forbidden") -> ClefsError:
    return ClefsError(403, detail, code)


def not_found(detail: str = "Not found", code: str = "not_found") -> ClefsError:
    return ClefsError(404, detail, code)


def conflict(detail: str, code: str = "conflict") -> ClefsError:
    return ClefsError(409, detail, code)


def bad_request(detail: str, code: str = "bad_request") -> ClefsError:
    return ClefsError(400, detail, code)


def too_many(detail: str = "Too many attempts, try again shortly", code: str = "rate_limited") -> ClefsError:
    return ClefsError(429, detail, code)


class NotConfigured(ClefsError):
    """A module api.py call whose feature is not built or not set up for this
    hotel yet. Callers (concierge tools, other modules) catch it and degrade."""

    def __init__(self, feature: str):
        super().__init__(409, f"{feature} is not configured for this hotel", "not_configured")
        self.feature = feature
