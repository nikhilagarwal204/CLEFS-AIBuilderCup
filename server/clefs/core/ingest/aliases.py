"""Static alias maps (ingest/aliases/<source>.yaml): which export header
means which canonical field (02 spec 2.2 step 3).

Every alias is a best guess marked UNVERIFIED in its file: no OTA publishes
its headers. A map is therefore only the first try; the mapping a person
confirms is saved as a template and wins next time.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from clefs.core.ingest.kinds import KINDS, schema_for
from clefs.core.ingest.sniff import normalize_header

ALIAS_DIR = Path(__file__).parent / "aliases"
GENERIC = "generic"
TRANSFORM_KEYS = ("date_fmt", "decimal_sep", "sign_flip", "pct_to_ratio")


@dataclass(frozen=True)
class AliasMap:
    source_code: str
    label: str
    kind: str  # "*" for generic
    ota_code: str | None
    unverified: bool
    detect: tuple[str, ...]
    fields: dict[str, tuple[str, ...]]  # {canonical: normalized aliases}, per kind for generic
    transforms: dict[str, dict] = field(default_factory=dict)
    by_kind: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)


def _norm_list(values: Any, where: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise ValueError(f"{where}: aliases must be a list")
    return tuple(dict.fromkeys(normalize_header(v) for v in values if normalize_header(v)))


def _check_fields(kind: str, fields: dict, where: str) -> None:
    names = schema_for(kind).names
    unknown = sorted(set(fields) - set(names))
    if unknown:
        raise ValueError(f"{where}: {unknown} are not {kind} fields")


def _parse(path: Path) -> AliasMap:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    where = path.name
    code = doc.get("source_code")
    if code != path.stem:
        raise ValueError(f"{where}: source_code must equal the file name")
    kind = doc.get("kind")
    transforms = doc.get("transforms") or {}
    if kind == "*":
        by_kind = {}
        for k, fmap in (doc.get("kinds") or {}).items():
            if k not in KINDS:
                raise ValueError(f"{where}: unknown kind {k}")
            _check_fields(k, fmap, where)
            by_kind[k] = {f: _norm_list(v, f"{where}:{k}.{f}") for f, v in fmap.items()}
        return AliasMap(code, doc.get("label") or code, "*", None, bool(doc.get("unverified", True)), (), {},
                        {}, by_kind)
    if kind not in KINDS:
        raise ValueError(f"{where}: unknown kind {kind!r}")
    fields = doc.get("fields") or {}
    _check_fields(kind, fields, where)
    _check_fields(kind, transforms, where)
    for f, t in transforms.items():
        bad = set(t) - set(TRANSFORM_KEYS)
        if bad:
            raise ValueError(f"{where}: unknown transforms {sorted(bad)} on {f}")
    return AliasMap(
        source_code=code,
        label=doc.get("label") or code,
        kind=kind,
        ota_code=doc.get("ota_code"),
        unverified=bool(doc.get("unverified", True)),
        detect=_norm_list(doc.get("detect") or [], f"{where}:detect"),
        fields={f: _norm_list(v, f"{where}:{f}") for f, v in fields.items()},
        transforms={f: dict(t) for f, t in transforms.items()},
    )


@functools.cache
def load_maps() -> dict[str, AliasMap]:
    """Every alias map, validated against the kind schemas (a typo in a YAML
    field name fails at import, in tests, not silently at upload)."""
    return {m.source_code: m for m in (_parse(p) for p in sorted(ALIAS_DIR.glob("*.yaml")))}


def get_map(source_code: str | None) -> AliasMap | None:
    return load_maps().get(source_code or "")


def sources(kind: str | None = None) -> list[AliasMap]:
    """Named sources (never generic), of one kind when given."""
    return [m for m in load_maps().values() if m.kind != "*" and (kind is None or m.kind == kind)]


def source_kind(source_code: str) -> str | None:
    m = get_map(source_code)
    return m.kind if m and m.kind != "*" else None


# ─── Resolution ───


def aliases_for(kind: str, source_code: str | None = None) -> dict[str, tuple[str, ...]]:
    """{canonical: aliases in priority order}: the source's own spellings,
    then the generic ones for the kind, then the field's own name (with and
    without its _minor suffix)."""
    src = get_map(source_code)
    generic = get_map(GENERIC)
    out: dict[str, tuple[str, ...]] = {}
    # No named source (or generic): every named source of the kind lends
    # its spellings, so an unlabelled PMS export still finds "arrival date".
    pool = [src] if src and src.kind == kind else sources(kind)
    for f in schema_for(kind).names:
        own: tuple[str, ...] = tuple(a for m in pool for a in m.fields.get(f, ()))
        gen = generic.by_kind.get(kind, {}).get(f, ()) if generic else ()
        names = (normalize_header(f), normalize_header(f.removesuffix("_minor")))
        out[f] = tuple(dict.fromkeys(own + gen + names))
    return out


def _contains(header: str, alias: str) -> bool:
    return f" {alias} " in f" {header} "


def resolve(headers: list[str], kind: str, source_code: str | None = None) -> tuple[dict[str, str], dict[str, dict]]:
    """(mapping {canonical: header}, transforms) from the alias map. Exact
    normalized matches first, field by field in schema order; then, for a
    field still open, a multi-word alias inside a longer header ("check in"
    in "check in date"). A header serves one field only."""
    by_norm: dict[str, str] = {}
    for h in headers:
        by_norm.setdefault(normalize_header(h), h)
    table = aliases_for(kind, source_code)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    # Pass 1 goes alias rank by rank across fields, so a field's first
    # spelling beats another field's third spelling of the same header.
    depth = max((len(a) for a in table.values()), default=0)
    for rank in range(depth):
        for f, aliases in table.items():
            if f in mapping or rank >= len(aliases):
                continue
            h = by_norm.get(aliases[rank])
            if h is not None and h not in used:
                mapping[f] = h
                used.add(h)
    for f, aliases in table.items():
        if f in mapping:
            continue
        for a in aliases:
            if " " not in a:
                continue
            h = next((orig for n, orig in by_norm.items() if orig not in used and _contains(n, a)), None)
            if h is not None:
                mapping[f] = h
                used.add(h)
                break
    mapping = {f: mapping[f] for f in table if f in mapping}  # schema order, stable output
    src = get_map(source_code)
    transforms = {f: dict(t) for f, t in (src.transforms if src and src.kind == kind else {}).items() if f in mapping}
    return mapping, transforms


def missing_required(mapping: dict[str, str], kind: str) -> list[str]:
    return [f for f in schema_for(kind).required if f not in mapping]


def detect_source(headers: list[str], kind: str | None = None) -> str | None:
    """The named source whose `detect` terms all appear (as a header or a
    phrase inside one), scored by how many of its aliases match exactly
    (the email path, where nobody said what the attachment is)."""
    norms = {normalize_header(h) for h in headers}
    best, best_score = None, 0
    for m in sources(kind):
        if not m.detect or not all(any(_contains(n, d) for n in norms) for d in m.detect):
            continue
        score = sum(1 for aliases in m.fields.values() if any(a in norms for a in aliases))
        if score > best_score:
            best, best_score = m.source_code, score
    return best
