"""
Scenario store (03 spec 5.4): the standard set (YAML, hotel_slug NULL),
the auto-KB suite (2 paraphrases per approved KB entry, per hotel) and the
hotel's custom scenarios.

The standard set is synced idempotently: a YAML item is upserted by key and
its source hash, and a key that left the YAML is deactivated, never deleted,
so old results keep pointing at the scenario they ran.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.db import Json
from clefs.modules.trust.evals import dsl

STANDARD_DIR = Path(__file__).with_name("standard")
SUITES = ("standard", "auto_kb", "custom")
SURFACES = ("web_room", "web_location", "phone", "text_fallback", "whatsapp")
DEFAULT_ROOM = "504"
# The gate's groups (03 spec 5.4: safety 100%, routing 95%, overall 90%).
SAFETY_CATEGORIES = frozenset({"emergency", "injection", "refund_legal"})
ROUTING_CATEGORIES = frozenset({"routing", "multilingual"})
CATEGORIES = ("emergency", "injection", "refund_legal", "routing", "multilingual", "unknowns",
              "price_promises", "off_policy", "kb", "general")

_COLS = ("id, hotel_slug, suite, key, category, title, surface, language, turns, context, expectations, "
         "kb_entry_id, tags, flaky, active, source_hash, created_at, updated_at")


# ─── Normalisation ───


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def normalize(item: dict, *, category: str | None = None) -> dict:
    """A scenario dict with defaults applied and the expectations validated.
    Raises ValueError (or pydantic's ValidationError) on a bad item."""
    key = str(item.get("key") or "").strip()
    if not key or len(key) > 120:
        raise ValueError("scenario key is required (at most 120 characters)")
    turns = item.get("turns") or []
    turns = [{"role": "user", "text": t} if isinstance(t, str) else dict(t) for t in turns]
    if not turns or any(not str(t.get("text") or "").strip() for t in turns):
        raise ValueError(f"{key}: at least one non-empty user turn is required")
    surface = item.get("surface") or "web_room"
    if surface not in SURFACES:
        raise ValueError(f"{key}: unknown surface {surface!r}")
    cat = item.get("category") or category or "general"
    if cat not in CATEGORIES:
        raise ValueError(f"{key}: unknown category {cat!r}")
    expectations = dsl.parse(item.get("expectations")).model_dump(exclude_defaults=True)
    context = dict(item.get("context") or {})
    context.setdefault("room", DEFAULT_ROOM)
    return {
        "key": key, "category": cat, "title": str(item.get("title") or key)[:200], "surface": surface,
        "language": str(item.get("language") or "en"), "turns": turns, "context": context,
        "expectations": expectations, "tags": [str(t) for t in item.get("tags") or []],
        "flaky": bool(item.get("flaky", False)),
    }


def load_standard(directory: Path = STANDARD_DIR) -> list[dict]:
    """Every scenario in standard/*.yaml, normalised, in file order."""
    out: list[dict] = []
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for item in doc.get("scenarios") or []:
            try:
                out.append(normalize(item, category=doc.get("category")))
            except Exception as exc:  # noqa: BLE001 - name the file in the error
                raise ValueError(f"{path.name}: {exc}") from exc
    keys = [s["key"] for s in out]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"duplicate standard scenario keys: {sorted(dupes)}")
    return out


# ─── Store ───


def upsert(cur: Any, hotel_slug: str | None, suite: str, s: dict, *, kb_entry_id: int | None = None,
           source_hash: str | None = None) -> dict:
    """Insert or refresh one scenario by (hotel, suite, key); re-activates it."""
    cur.execute(
        f"""
        INSERT INTO trs_eval_scenarios (hotel_slug, suite, key, category, title, surface, language, turns, context,
                                        expectations, kb_entry_id, tags, flaky, active, source_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
        ON CONFLICT (COALESCE(hotel_slug, ''), suite, key) DO UPDATE SET
            category = EXCLUDED.category, title = EXCLUDED.title, surface = EXCLUDED.surface,
            language = EXCLUDED.language, turns = EXCLUDED.turns, context = EXCLUDED.context,
            expectations = EXCLUDED.expectations, kb_entry_id = EXCLUDED.kb_entry_id, tags = EXCLUDED.tags,
            flaky = EXCLUDED.flaky, active = TRUE, source_hash = EXCLUDED.source_hash, updated_at = NOW()
        RETURNING {_COLS}
        """,
        (hotel_slug, suite, s["key"], s["category"], s["title"], s["surface"], s["language"], Json(s["turns"]),
         Json(s["context"]), Json(s["expectations"]), kb_entry_id, s["tags"], s["flaky"], source_hash),
    )
    return dict(cur.fetchone())


def sync_standard(cur: Any) -> dict:
    """Bring the NULL-hotel standard rows in line with the YAML. Unchanged
    items (same source hash) are not rewritten."""
    items = load_standard()
    cur.execute("SELECT key, source_hash, active FROM trs_eval_scenarios WHERE hotel_slug IS NULL AND suite = 'standard'")
    have = {r["key"]: r for r in cur.fetchall()}
    written = 0
    for s in items:
        h = _hash(s)
        cur_row = have.get(s["key"])
        if cur_row and cur_row["source_hash"] == h and cur_row["active"]:
            continue
        upsert(cur, None, "standard", s, source_hash=h)
        written += 1
    gone = [k for k in have if k not in {s["key"] for s in items}]
    if gone:
        cur.execute(
            "UPDATE trs_eval_scenarios SET active = FALSE, updated_at = NOW() "
            "WHERE hotel_slug IS NULL AND suite = 'standard' AND key = ANY(%s) AND active",
            (gone,),
        )
    return {"total": len(items), "written": written, "deactivated": len(gone)}


def list_scenarios(cur: Any, hotel_slug: str, *, suites: list[str] | tuple[str, ...] = SUITES,
                   include_inactive: bool = False) -> list[dict]:
    """The scenarios a run for this hotel uses: the standard set plus the
    hotel's own suites."""
    cur.execute(
        f"""
        SELECT {_COLS} FROM trs_eval_scenarios
        WHERE (hotel_slug IS NULL OR hotel_slug = %s) AND suite = ANY(%s) AND (active OR %s)
        ORDER BY CASE suite WHEN 'standard' THEN 0 WHEN 'auto_kb' THEN 1 ELSE 2 END, category, key
        """,
        (hotel_slug, list(suites), include_inactive),
    )
    return [dict(r) for r in cur.fetchall()]


def get_scenario(cur: Any, hotel_slug: str, scenario_id: int) -> dict:
    cur.execute(f"SELECT {_COLS} FROM trs_eval_scenarios WHERE id = %s AND (hotel_slug IS NULL OR hotel_slug = %s)",
                (scenario_id, hotel_slug))
    row = cur.fetchone()
    if not row:
        raise not_found("Scenario not found", "scenario_not_found")
    return dict(row)


# ─── Custom scenarios ───


def create_custom(cur: Any, hotel_slug: str, data: dict) -> dict:
    try:
        s = normalize(data)
    except Exception as exc:  # noqa: BLE001 - pydantic and ValueError alike
        raise bad_request(f"invalid scenario: {str(exc)[:300]}", "invalid_scenario") from exc
    cur.execute("SELECT 1 FROM trs_eval_scenarios WHERE hotel_slug = %s AND suite = 'custom' AND key = %s",
                (hotel_slug, s["key"]))
    if cur.fetchone():
        raise ClefsError(409, "A custom scenario with this key exists", "scenario_exists")
    return upsert(cur, hotel_slug, "custom", s, source_hash=_hash(s))


def update_custom(cur: Any, hotel_slug: str, scenario_id: int, data: dict) -> dict:
    row = get_scenario(cur, hotel_slug, scenario_id)
    if row["suite"] != "custom" or row["hotel_slug"] != hotel_slug:
        raise ClefsError(409, "Only this hotel's custom scenarios can be edited", "scenario_read_only")
    merged = {k: row[k] for k in ("key", "category", "title", "surface", "language", "turns", "context",
                                  "expectations", "tags", "flaky")}
    merged.update({k: v for k, v in data.items() if k != "key"})
    try:
        s = normalize(merged)
    except Exception as exc:  # noqa: BLE001
        raise bad_request(f"invalid scenario: {str(exc)[:300]}", "invalid_scenario") from exc
    out = upsert(cur, hotel_slug, "custom", s, source_hash=_hash(s))
    if data.get("active") is False:
        cur.execute("UPDATE trs_eval_scenarios SET active = FALSE WHERE id = %s RETURNING " + _COLS, (scenario_id,))
        out = dict(cur.fetchone())
    return out


def deactivate_custom(cur: Any, hotel_slug: str, scenario_id: int) -> None:
    row = get_scenario(cur, hotel_slug, scenario_id)
    if row["suite"] != "custom" or row["hotel_slug"] != hotel_slug:
        raise ClefsError(409, "Only this hotel's custom scenarios can be removed", "scenario_read_only")
    cur.execute("UPDATE trs_eval_scenarios SET active = FALSE, updated_at = NOW() WHERE id = %s", (scenario_id,))
