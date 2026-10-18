"""
Route walking that survives FastAPI's nested routers.

Up to 0.136, include_router() copies every route into app.routes (and into a
parent APIRouter's .routes) with its prefix applied. From 0.137.0 it appends
one _IncludedRouter entry instead, whose effective_route_contexts() yield the
mounted routes: an APIRoute context carries .path/.methods/.name itself, and a
plain Starlette route comes back rebuilt (prefix applied) as .starlette_route.
A walker that reads app.routes directly would see none of the routes there,
and "no duplicate route" / "every path is under its prefix" checks would then
pass by inspecting nothing. Walk through iter_routes() instead.

Checked on both 0.135.3 and 0.137.0 (tests/foundation/test_w0b_ops.py).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any


def iter_routes(routes: Iterable[Any]) -> Iterator[Any]:
    """Every mounted route, flattened, each with its full path."""
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for ctx in contexts():
                yield ctx.starlette_route if ctx.starlette_route is not None else ctx
        else:
            yield route


def route_paths(routes: Iterable[Any]) -> set[str]:
    return {getattr(r, "path", "") for r in iter_routes(routes)}
