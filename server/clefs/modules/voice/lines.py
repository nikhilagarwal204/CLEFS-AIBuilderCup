"""
Voice lines and the extension allowlist (03 spec 3.1-3.2, 3.6 steps 2-3).

A line is one hotel's SIP entry point. Anyone who knows an SIP trunk id
can dial sip:<project>@sip.voice.clefs.app, so a call is served only when a
line claims it: preferably by the X-Clefs-Line header secret the PBX or the
bridge injects (stored as a sha256 digest), else by an allowed source (the
From domain, or a Via/Contact host or IP) on a line of the dialled project.
The SIP From user part is text the caller controls; it becomes a room only
through vc_extensions, and only on a call that carried the line secret
(every source host is caller-written too, so a source-identified call
always asks for the room and its requests stay unverified).
"""

from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import re
import secrets
from datetime import datetime, time as dtime
from typing import Any

from clefs import ratelimit
from clefs.core import clock
from clefs.core import rooms as rooms_mod
from clefs.core.errors import bad_request, not_found
from clefs.core.hotels import Hotel
from clefs.db import Json
from clefs.modules.voice import constants as C

LINE_FIELDS = (
    "label", "trunk_id", "allowed_sources", "pbx_kind", "caller_id_mode", "front_desk_uri",
    "outside_line_prefix", "default_language", "model", "voice", "max_call_s", "max_concurrent",
    "tool_transport", "enabled", "after_hours",
)
JSON_FIELDS = ("allowed_sources", "after_hours", "extension_rule")
SIP_URI_RE = re.compile(r"^sips?:[^\s]+$", re.IGNORECASE)
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EXT_RE = re.compile(r"^[0-9A-Za-z*#+._-]{1,32}$")
LANG_RE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]{3,80}$")


# ─── Secrets ───


def new_line_secret() -> str:
    """The X-Clefs-Line value, shown once when a line is created or rotated."""
    return "vcl_" + secrets.token_urlsafe(24)


def secret_hash(secret: str) -> str:
    return hashlib.sha256((secret or "").strip().encode()).hexdigest()


def sip_uri(project_id: str | None) -> str | None:
    """What the PBX trunk dials (03 spec 3.6 step 2)."""
    if not project_id:
        return None
    return f"sip:{project_id}@{C.BRIDGE_SIP_HOST};transport=tls"


# ─── Validation ───


def _source_ok(value: str) -> bool:
    """A domain (pbx.hotel.ae), an IP, or a CIDR."""
    v = value.strip().lower()
    if not v or len(v) > 253:
        return False
    try:
        ipaddress.ip_network(v, strict=False)
        return True
    except ValueError:
        pass
    return bool(re.match(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}$", v))


def _after_hours(value: Any) -> dict:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise bad_request("after_hours must be an object", "invalid_after_hours")
    enabled = bool(value.get("enabled"))
    start, end = str(value.get("start") or ""), str(value.get("end") or "")
    if enabled and not (HHMM_RE.match(start) and HHMM_RE.match(end)) or start == end and enabled:
        raise bad_request("after_hours needs start and end as HH:MM, and they must differ", "invalid_after_hours")
    return {"enabled": enabled, "start": start or None, "end": end or None}


def clean_line(data: dict, *, partial: bool) -> dict:
    """Validated column values from a console body. 400 with a precise code
    for each rule the table's CHECKs also enforce."""
    out: dict[str, Any] = {}
    for key in LINE_FIELDS:
        if key not in data:
            continue
        v = data[key]
        if key == "label":
            v = " ".join(str(v or "").split())[:80]
            if not v:
                raise bad_request("label is required", "label_required")
        elif key == "trunk_id":
            v = (str(v).strip() or None) if v is not None else None
            if v and not PROJECT_RE.match(v):
                raise bad_request("trunk_id looks wrong", "invalid_project_id")
        elif key == "allowed_sources":
            if not isinstance(v, list) or len(v) > 20 or not all(isinstance(s, str) and _source_ok(s) for s in v):
                raise bad_request("allowed_sources is a list of domains, IPs or CIDRs (at most 20)", "invalid_allowed_sources")
            v = [s.strip().lower() for s in v]
        elif key == "pbx_kind":
            if v not in C.PBX_KINDS:
                raise bad_request(f"pbx_kind must be one of {', '.join(C.PBX_KINDS)}", "invalid_pbx_kind")
        elif key == "caller_id_mode":
            if v not in C.CALLER_ID_MODES:
                raise bad_request("caller_id_mode must be extension or none", "invalid_caller_id_mode")
        elif key == "front_desk_uri":
            v = (str(v).strip() or None) if v is not None else None
            if v is not None and not valid_refer_target(v):
                raise bad_request("front_desk_uri must be a sip: or sips: URI (never tel:)", "invalid_front_desk_uri")
        elif key == "outside_line_prefix":
            v = (str(v).strip() or None) if v is not None else None
            if v is not None and not re.match(r"^[0-9*#]{1,4}$", v):
                raise bad_request("outside_line_prefix is 1-4 digits", "invalid_outside_line_prefix")
        elif key == "default_language":
            v = str(v or "en").strip()
            if not LANG_RE.match(v):
                raise bad_request("default_language is a language code such as en or ar", "invalid_language")
        elif key in ("model", "voice"):
            v = (str(v).strip()[:80] or None) if v is not None else None
        elif key == "max_call_s":
            if not isinstance(v, int) or isinstance(v, bool) or not 60 <= v <= C.MAX_CALL_S_LIMIT:
                raise bad_request(f"max_call_s must be 60-{C.MAX_CALL_S_LIMIT}", "invalid_max_call_s")
        elif key == "max_concurrent":
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 50:
                raise bad_request("max_concurrent must be 1-50", "invalid_max_concurrent")
        elif key == "tool_transport":
            if v not in C.TOOL_TRANSPORTS:
                raise bad_request("tool_transport must be mcp or sideband", "invalid_tool_transport")
        elif key == "enabled":
            v = bool(v)
        elif key == "after_hours":
            v = _after_hours(v)
        out[key] = v
    if not partial and "label" not in out:
        raise bad_request("label is required", "label_required")
    return out


def _check_sources_have_project(line: dict) -> None:
    """identify_line matches allowed_sources only on lines of the dialled
    project, so sources on a line without a project id would never match:
    say so at save time instead of 486ing every call later."""
    if (line.get("allowed_sources") or []) and not line.get("trunk_id"):
        raise bad_request("Allowed sources need the SIP trunk id: a call is matched only to lines of the "
                          "project it dialled", "sources_need_project")


def valid_refer_target(uri: str | None) -> bool:
    """sip: or sips: only. tel: (and anything else) is refused: PSTN
    breakout through the telephony bridge is a UAE regulatory grey area (03 spec 3.3)."""
    return bool(uri) and bool(SIP_URI_RE.match(uri.strip())) and len(uri) <= 255


# ─── Lines CRUD ───


def public_line(row: dict, *, extensions: int | None = None) -> dict:
    """A line for the console: never the secret digest, only its hint."""
    out = {k: v for k, v in dict(row).items() if k != "line_secret_hash"}
    out["has_secret"] = bool(row.get("line_secret_hash"))
    out["sip_uri"] = sip_uri(row.get("trunk_id"))
    if extensions is not None:
        out["extensions"] = extensions
    return out


def list_lines(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        """
        SELECT l.*, (SELECT count(*) FROM vc_extensions e WHERE e.voice_line_id = l.id) AS extension_count
        FROM vc_lines l WHERE l.hotel_slug = %s ORDER BY l.id
        """,
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def get_line(cur: Any, hotel_slug: str, line_id: int, *, lock: bool = False) -> dict:
    cur.execute(
        "SELECT * FROM vc_lines WHERE id = %s AND hotel_slug = %s" + (" FOR UPDATE" if lock else ""),
        (int(line_id), hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        raise not_found("Voice line not found", "line_not_found")
    return dict(row)


def line_by_id(cur: Any, line_id: int | None) -> dict | None:
    if not line_id:
        return None
    cur.execute("SELECT * FROM vc_lines WHERE id = %s", (int(line_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def create_line(cur: Any, hotel_slug: str, data: dict) -> tuple[dict, str]:
    """(row, raw secret). The secret is returned once; only its digest is kept."""
    values = clean_line(data, partial=False)
    values.setdefault("trunk_id", None)
    _check_sources_have_project(values)
    secret = new_line_secret()
    values["line_secret_hash"] = secret_hash(secret)
    values["line_secret_hint"] = secret[-4:]
    cols = ["hotel_slug", *values]
    params = [hotel_slug, *(Json(values[k]) if k in JSON_FIELDS else values[k] for k in values)]
    cur.execute(
        f"INSERT INTO vc_lines ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",
        params,
    )
    return dict(cur.fetchone()), secret


def update_line(cur: Any, hotel_slug: str, line_id: int, data: dict) -> tuple[dict, dict]:
    """(before, after)."""
    before = get_line(cur, hotel_slug, line_id, lock=True)
    values = clean_line(data, partial=True)
    if not values:
        return before, before
    if "allowed_sources" in values or "trunk_id" in values:
        # Only when the patch touches them: an older line saved before this
        # rule can still be switched off or relabelled.
        _check_sources_have_project({**before, **values})
    sets = ", ".join(f"{k} = %s" for k in values)
    params = [Json(values[k]) if k in JSON_FIELDS else values[k] for k in values]
    cur.execute(
        f"UPDATE vc_lines SET {sets}, updated_at = %s WHERE id = %s AND hotel_slug = %s RETURNING *",
        (*params, clock.now(), int(line_id), hotel_slug),
    )
    return before, dict(cur.fetchone())


def rotate_secret(cur: Any, hotel_slug: str, line_id: int) -> tuple[dict, str]:
    get_line(cur, hotel_slug, line_id, lock=True)
    secret = new_line_secret()
    cur.execute(
        "UPDATE vc_lines SET line_secret_hash = %s, line_secret_hint = %s, updated_at = %s WHERE id = %s RETURNING *",
        (secret_hash(secret), secret[-4:], clock.now(), int(line_id)),
    )
    return dict(cur.fetchone()), secret


def delete_line(cur: Any, hotel_slug: str, line_id: int) -> dict:
    row = get_line(cur, hotel_slug, line_id, lock=True)
    cur.execute("DELETE FROM vc_lines WHERE id = %s", (int(line_id),))
    return row


# ─── SIP headers ───

URI_RE = re.compile(r"sips?:(?:(?P<user>[^@;>\s]+)@)?(?P<host>\[[0-9a-fA-F:.]+\]|[^;>:\s]+)", re.IGNORECASE)
VIA_RE = re.compile(r"SIP/2\.0/[A-Z]+\s+(?P<host>\[[0-9a-fA-F:.]+\]|[^;:\s]+)", re.IGNORECASE)
RECEIVED_RE = re.compile(r";\s*received=(?P<ip>[0-9a-fA-F:.]+)", re.IGNORECASE)


def header_map(sip_headers: list | None) -> dict[str, list[str]]:
    """[{name, value}] -> {lowercased name: [values]} (a header may repeat)."""
    out: dict[str, list[str]] = {}
    for h in sip_headers or []:
        name = h.get("name") if isinstance(h, dict) else getattr(h, "name", None)
        value = h.get("value") if isinstance(h, dict) else getattr(h, "value", None)
        if not name or value is None:
            continue
        out.setdefault(str(name).strip().lower(), []).append(str(value)[:1000])
    return out


def parse_uri(value: str | None) -> tuple[str | None, str | None]:
    """(user, host) of the first sip:/sips: URI in a header value."""
    m = URI_RE.search(value or "")
    if not m:
        return None, None
    user = (m.group("user") or "").strip() or None
    host = m.group("host").strip("[]").lower()
    return user, host


def source_hosts(headers: dict[str, list[str]]) -> list[str]:
    """Every host a line's allowed_sources may match: the From domain, the
    Via sent-by hosts and received= IPs, and the Contact host."""
    hosts: list[str] = []
    for name in ("from", "contact"):
        for v in headers.get(name, []):
            _, host = parse_uri(v)
            if host:
                hosts.append(host)
    for v in headers.get("via", []):
        for part in v.split(","):
            m = VIA_RE.search(part)
            if m:
                hosts.append(m.group("host").strip("[]").lower())
            r = RECEIVED_RE.search(part)
            if r:
                hosts.append(r.group("ip").lower())
    return list(dict.fromkeys(hosts))


def from_user(headers: dict[str, list[str]]) -> str | None:
    """The caller's extension as the PBX put it in From (untrusted text)."""
    for name in ("from", "p-asserted-identity", "remote-party-id"):
        for v in headers.get(name, []):
            user, _ = parse_uri(v)
            if user:
                return user[:64]
    return None


def to_project(headers: dict[str, list[str]]) -> str | None:
    for v in headers.get("to", []):
        user, host = parse_uri(v)
        if user and host and host.endswith(C.BRIDGE_SIP_HOST):
            return user[:80]
    return None


def redacted_headers(headers: dict[str, list[str]]) -> dict[str, list[str]]:
    """Headers worth keeping for support, without secrets: the line header's
    value is replaced by a marker and unknown headers are dropped."""
    out = {k: [v[:300] for v in vals] for k, vals in headers.items() if k in C.KEPT_HEADERS}
    if C.LINE_HEADER in headers:
        out[C.LINE_HEADER] = ["[present]"]
    return out


def _host_matches(host: str, source: str) -> bool:
    try:
        net = ipaddress.ip_network(source, strict=False)
    except ValueError:
        return host == source or host.endswith("." + source)
    try:
        return ipaddress.ip_address(host) in net
    except ValueError:
        return False


# ─── Identification and admission ───


def identify_line(cur: Any, headers: dict[str, list[str]]) -> tuple[dict | None, str]:
    """(line, how) where how is header|source, or (None, reason) with reason
    one of bad_line_secret, ambiguous_source, unidentified. A line header
    that matches nothing is refused outright rather than falling back to the
    sources: a rotated secret must be fixed on the PBX, not silently ignored.

    'source' is WEAK identification. The From, Contact and Via hosts are all
    written by the caller's user agent, so anyone who can reach the SIP URI
    can claim pbx.hotel.ae. It therefore (a) only ever picks among lines of
    the SIP trunk the call was actually placed to (a line without a
    project id is never source-identified, and a project mismatch never
    falls back to a host-only match), and (b) never makes the From extension
    a trusted room: see extension_trusted()."""
    secret = (headers.get(C.LINE_HEADER) or [None])[0]
    if secret:
        cur.execute("SELECT * FROM vc_lines WHERE line_secret_hash = %s", (secret_hash(secret),))
        row = cur.fetchone()
        return (dict(row), "header") if row else (None, "bad_line_secret")
    hosts = source_hosts(headers)
    project = to_project(headers)
    if not hosts or not project:
        return None, "unidentified"
    cur.execute(
        "SELECT * FROM vc_lines WHERE trunk_id = %s AND jsonb_array_length(allowed_sources) > 0 ORDER BY id",
        (project,),
    )
    matches = [
        dict(r) for r in cur.fetchall()
        if any(_host_matches(h, str(s).lower()) for h in hosts for s in (r["allowed_sources"] or []))
    ]
    if len(matches) == 1:
        return matches[0], "source"
    return None, ("ambiguous_source" if matches else "unidentified")


def extension_trusted(how: str) -> bool:
    """Whether the From extension may become a verified room. Only a call
    that carried the line's X-Clefs-Line secret: the PBX (or bridge) that
    holds the secret also wrote From, so its extension is the handset's.
    Without it the From user part is whatever the caller typed; the call is
    served but the room is asked for and requests stay unverified."""
    return how == "header"


def in_after_hours(line: dict, hotel: Hotel, at: datetime | None = None) -> bool:
    """True inside the line's after-hours window (hotel-local HH:MM; a window
    may cross midnight, 23:00-06:00)."""
    policy = line.get("after_hours") or {}
    if not policy.get("enabled") or not policy.get("start") or not policy.get("end"):
        return False
    local = (at or clock.now()).astimezone(hotel.tz).time().replace(second=0, microsecond=0)
    start, end = (dtime.fromisoformat(policy["start"]), dtime.fromisoformat(policy["end"]))
    return start <= local < end if start < end else (local >= start or local < end)


def live_calls(cur: Any, line: dict) -> int:
    """Calls holding a slot on this line. A call whose end nobody reported
    (no worker, no call-end event) stops counting after max_call_s."""
    cur.execute(
        """
        SELECT count(*) AS n FROM vc_calls
        WHERE voice_line_id = %s AND status = ANY(%s) AND ended_at IS NULL
          AND started_at > %s - make_interval(secs => %s)
        """,
        (line["id"], list(C.LIVE_STATUSES), clock.now(), int(line["max_call_s"]) + 60),
    )
    return int(cur.fetchone()["n"])


def admit(cur: Any, line: dict, hotel: Hotel, *, voice_enabled: bool, module_on: bool) -> str | None:
    """None to accept, else the reason for the 486."""
    if not voice_enabled:
        return "voice_disabled"
    if not module_on:
        return "module_disabled"
    if not line.get("enabled"):
        return "line_disabled"
    if in_after_hours(line, hotel):
        return "after_hours"
    # Serialize admissions per line for the rest of this (short) decision
    # transaction, so two simultaneous INVITEs cannot both take the last slot.
    cur.execute("SELECT id FROM vc_lines WHERE id = %s FOR UPDATE", (line["id"],))
    if live_calls(cur, line) >= int(line["max_concurrent"]):
        return "over_capacity"
    return None


def normalize_extension(value: str | None) -> str | None:
    v = (value or "").strip()
    if v.lower().startswith("tel:"):
        v = v[4:]
    v = v.split(";")[0].strip()
    return v if v and EXT_RE.match(v) else None


def map_extension(cur: Any, line: dict, ext: str | None) -> dict | None:
    """The vc_extensions row for the caller's extension, or None. Only the
    allowlist maps a room: the From header itself is never trusted."""
    ext = normalize_extension(ext)
    if not ext or line.get("caller_id_mode") == "none":
        return None
    cur.execute("SELECT * FROM vc_extensions WHERE voice_line_id = %s AND extension = %s", (line["id"], ext))
    row = cur.fetchone()
    return dict(row) if row else None


def extension_rate_ok(cur: Any, line: dict, ext: str | None, *, trusted: bool = True) -> bool:
    """At most 10 calls an hour from one extension (a stuck handset or a
    prank loop should not tie up the line). Untrusted (source-identified)
    calls count in their own bucket: otherwise ten spoofed INVITEs with
    From 1204 would lock the real room 1204 out of its own phone."""
    ext = normalize_extension(ext)
    if not ext:
        return True
    scope = "ext" if trusted else "ext-src"
    return ratelimit.hit(cur, f"voice:{scope}:{line['id']}:{ext}", limit=C.EXTENSION_CALLS_PER_HOUR, window_s=3600)


# ─── Extensions ───


def list_extensions(cur: Any, hotel_slug: str, line_id: int) -> list[dict]:
    get_line(cur, hotel_slug, line_id)
    cur.execute("SELECT * FROM vc_extensions WHERE voice_line_id = %s ORDER BY extension", (int(line_id),))
    return [dict(r) for r in cur.fetchall()]


def _room_ok(cur: Any, hotel_slug: str, room: str, register: bool) -> bool:
    if register:
        return rooms_mod.get_room(cur, hotel_slug, room) is not None
    return rooms_mod.valid_room(room)


def _location_ok(cur: Any, hotel_slug: str, slug: str) -> bool:
    cur.execute("SELECT 1 FROM hotel_locations WHERE hotel_slug = %s AND slug = %s", (hotel_slug, slug))
    return cur.fetchone() is not None


def _upsert_extension(cur: Any, hotel_slug: str, line_id: int, ext: str, *, room: str | None, location: str | None,
                      kind: str, source: str) -> dict:
    cur.execute(
        """
        INSERT INTO vc_extensions (hotel_slug, voice_line_id, extension, room, location_slug, kind, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (voice_line_id, extension) DO UPDATE
          SET room = EXCLUDED.room, location_slug = EXCLUDED.location_slug, kind = EXCLUDED.kind, source = EXCLUDED.source
        RETURNING *, (xmax = 0) AS inserted
        """,
        (hotel_slug, int(line_id), ext, room, location, kind, source),
    )
    return dict(cur.fetchone())


def add_extension(cur: Any, hotel_slug: str, line_id: int, data: dict, *, source: str = "manual") -> dict:
    """One mapping. kind room needs a room in the register (or a plausible
    room number when the hotel has no register yet); kind location a
    hotel_locations slug."""
    get_line(cur, hotel_slug, line_id)
    ext = normalize_extension(str(data.get("extension") or ""))
    if not ext:
        raise bad_request("extension is 1-32 digits or letters", "invalid_extension")
    kind = data.get("kind") or ("location" if data.get("location") and not data.get("room") else "room")
    if kind not in C.EXTENSION_KINDS:
        raise bad_request(f"kind must be one of {', '.join(C.EXTENSION_KINDS)}", "invalid_kind")
    room = (str(data.get("room") or "").strip() or None)
    location = (str(data.get("location") or "").strip() or None)
    if kind == "room" and not (room and _room_ok(cur, hotel_slug, room, rooms_mod.has_register(cur, hotel_slug))):
        raise bad_request(f"room {room!r} is not in the room register", "unknown_room")
    if kind == "location" and not (location and _location_ok(cur, hotel_slug, location)):
        raise bad_request(f"location {location!r} does not exist", "unknown_location")
    row = _upsert_extension(cur, hotel_slug, line_id, ext, room=room if kind == "room" else None,
                            location=location if kind == "location" else None, kind=kind, source=source)
    row.pop("inserted", None)
    return row


def delete_extension(cur: Any, hotel_slug: str, line_id: int, extension_id: int) -> dict:
    get_line(cur, hotel_slug, line_id)
    cur.execute(
        "DELETE FROM vc_extensions WHERE id = %s AND voice_line_id = %s RETURNING *",
        (int(extension_id), int(line_id)),
    )
    row = cur.fetchone()
    if row is None:
        raise not_found("Extension not found", "extension_not_found")
    return dict(row)


def import_csv(cur: Any, hotel_slug: str, line_id: int, text: str, *, replace: bool = False) -> dict:
    """extension,room[,kind[,location]] per line; a header row is optional.
    Bad rows are skipped and reported, good rows upserted. replace=True
    first drops the line's csv-sourced rows (manual and rule rows stay)."""
    get_line(cur, hotel_slug, line_id, lock=True)
    rows = list(csv.reader(io.StringIO(text or "")))
    if len(rows) > C.CSV_MAX_ROWS:
        raise bad_request(f"at most {C.CSV_MAX_ROWS} rows", "csv_too_large")
    has_header = bool(rows and rows[0] and rows[0][0].strip().lower() in ("extension", "ext", "extn"))
    if has_header:
        header = ["extension" if h.strip().lower() in ("ext", "extn") else h.strip().lower() for h in rows[0]]
        rows = rows[1:]
    else:
        header = ["extension", "room", "kind", "location"]
    if replace:
        cur.execute("DELETE FROM vc_extensions WHERE voice_line_id = %s AND source = 'csv'", (int(line_id),))
    imported, skipped = 0, []
    for n, raw in enumerate(rows, start=2 if has_header else 1):
        if not any(c.strip() for c in raw):
            continue
        rec = {header[i]: raw[i].strip() for i in range(min(len(header), len(raw)))}
        data = {"extension": rec.get("extension"), "room": rec.get("room"), "kind": rec.get("kind") or None,
                "location": rec.get("location")}
        cur.execute("SAVEPOINT vc_csv_row")
        try:
            add_extension(cur, hotel_slug, line_id, data, source="csv")
        except Exception as exc:  # a bad row never aborts the import
            cur.execute("ROLLBACK TO SAVEPOINT vc_csv_row")
            skipped.append({"line": n, "extension": data["extension"], "reason": getattr(exc, "code", None) or "invalid_row"})
            continue
        cur.execute("RELEASE SAVEPOINT vc_csv_row")
        imported += 1
    return {"imported": imported, "skipped": skipped[:200], "skipped_count": len(skipped)}


def clean_rule(data: dict) -> dict:
    prefix = str(data.get("prefix") or "")
    suffix = str(data.get("suffix") or "")
    strip = str(data.get("strip_room_prefix") or "")
    if any(len(x) > 8 or (x and not re.match(r"^[0-9A-Za-z*#]+$", x)) for x in (prefix, suffix, strip)):
        raise bad_request("prefix, suffix and strip_room_prefix are up to 8 digits or letters", "invalid_rule")
    return {"prefix": prefix, "suffix": suffix, "strip_room_prefix": strip,
            "use_room_extension": bool(data.get("use_room_extension"))}


def rule_extension(rule: dict, room: dict) -> str | None:
    """extension = prefix + room + suffix, or the register's own extension
    column when use_room_extension is on and the room has one."""
    if rule.get("use_room_extension") and room.get("extension"):
        return normalize_extension(str(room["extension"]))
    number = str(room["room_number"])
    strip = rule.get("strip_room_prefix") or ""
    if strip and number.startswith(strip):
        number = number[len(strip):]
    return normalize_extension(f"{rule.get('prefix') or ''}{number}{rule.get('suffix') or ''}")


def apply_rule(cur: Any, hotel_slug: str, line_id: int, data: dict, *, replace: bool = False, dry_run: bool = False) -> dict:
    """Materialise "extension = room" for every active room in the register.
    Rows are written (not computed per call) so the allowlist stays explicit
    and the console shows exactly which extension reaches which room."""
    get_line(cur, hotel_slug, line_id, lock=True)
    rule = clean_rule(data)
    register = rooms_mod.list_rooms(cur, hotel_slug)
    if not register:
        raise bad_request("the hotel has no room register yet; import rooms first or use a CSV", "no_room_register")
    pairs = [(rule_extension(rule, r), r["room_number"]) for r in register]
    good = [(e, r) for e, r in pairs if e]
    preview = [{"extension": e, "room": r} for e, r in good[:20]]
    if dry_run:
        return {"rule": rule, "rooms": len(register), "would_map": len(good), "preview": preview}
    if replace:
        cur.execute("DELETE FROM vc_extensions WHERE voice_line_id = %s AND source = 'rule'", (int(line_id),))
    created = updated = 0
    for ext, room in good:
        row = _upsert_extension(cur, hotel_slug, line_id, ext, room=room, location=None, kind="room", source="rule")
        created += 1 if row.get("inserted") else 0
        updated += 0 if row.get("inserted") else 1
    cur.execute("UPDATE vc_lines SET extension_rule = %s, updated_at = %s WHERE id = %s", (Json(rule), clock.now(), int(line_id)))
    return {"rule": rule, "rooms": len(register), "created": created, "updated": updated, "preview": preview}
