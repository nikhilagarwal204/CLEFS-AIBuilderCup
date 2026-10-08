"""
Bootstrap CLI.

    python -m clefs.cli create-hotel --slug demo-dubai --name "Demo Dubai" --country AE --emirate dubai \
        --tz Asia/Dubai --currency AED --locale en-AE
    python -m clefs.cli create-user --email owner@example.com --name "Owner" --phone +971500000000 --password-prompt
    python -m clefs.cli grant --email owner@example.com --hotel demo-dubai --role owner
    python -m clefs.cli make-admin --email me@clefs.world
    python -m clefs.cli pair-code --hotel demo-dubai --name "Floor 5" --kind floor --floors 5 --departments Housekeeping
    python -m clefs.cli set-pin --hotel demo-dubai --staff-id 12 --pin 1234
    python -m clefs.cli magic-link --email owner@example.com --hotel demo-dubai --purpose brief
    python -m clefs.cli seed-demo --hotel demo-deira-crest [--anchor 2026-09-27] [--password ...]
    python -m clefs.cli demo-present --name "Ramee Rose Hotel" [--city "Al Barsha"]
    python -m clefs.cli demo-check --hotel demo-deira-crest [--manifest ...] [--scale 1.0] [--no-jobs] [--commit]

Every command prints the target database (host and name, no password) first:
server/.env may point at production, so export DATABASE_URL for local work.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any
from types import SimpleNamespace

from clefs import db
from clefs.auth import devices, magic, passwords
from clefs.auth import permissions as perms_mod
from clefs.core import hotels
from clefs.core.errors import ClefsError
from clefs.core.phone import normalize_phone
from clefs.migrate import _redact
from clefs.settings import get_settings

CLI_ACTOR = SimpleNamespace(kind="system", user_id=None, device_id=None, label="cli")


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _user_id(cur: Any, email: str) -> int:
    cur.execute("SELECT id FROM users WHERE email = %s", (email.strip().lower(),))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no user with email {email}")
    return row["id"]


def cmd_create_hotel(a: argparse.Namespace) -> None:
    with db.db() as cur:
        h = hotels.create_hotel(
            cur,
            slug=a.slug,
            name=a.name,
            country=a.country,
            emirate=a.emirate or None,
            timezone=a.tz,
            currency=a.currency,
            locale=a.locale,
            city=a.city,
            star_rating=a.star_rating,
            rooms_count=a.rooms_count,
            status=a.status,
        )
    # No autonomy rows (04 spec 1.4): the hotel follows each type's
    # default_level until someone sets a dial or an incident demotes one.
    print(f"created hotel {h.slug} ({h.timezone}, {h.currency}, legacy_open_access={h.legacy_open_access})")


def cmd_create_user(a: argparse.Namespace) -> None:
    password = None
    if a.password_prompt:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            raise SystemExit("passwords do not match")
    elif a.password_env:
        password = os.environ.get(a.password_env)
        if not password:
            raise SystemExit(f"{a.password_env} is empty")
    if password:
        passwords.validate_password(password)
    with db.db() as cur:
        cur.execute(
            "INSERT INTO users (email, name, phone_e164, password_hash) VALUES (%s, %s, %s, %s) RETURNING id",
            (a.email.strip().lower(), a.name, normalize_phone(a.phone), passwords.hash_password(password) if password else None),
        )
        uid = cur.fetchone()["id"]
    print(f"created user {uid} <{a.email.lower()}>{'' if password else ' (no password: magic links only)'}")


def cmd_grant(a: argparse.Namespace) -> None:
    if a.role not in perms_mod.ROLES:
        raise SystemExit(f"role must be one of {', '.join(perms_mod.ROLES)}")
    with db.db() as cur:
        hotels.get_hotel(cur, a.hotel)
        uid = _user_id(cur, a.email)
        cur.execute(
            """
            INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, hotel_slug) DO UPDATE SET role = EXCLUDED.role, departments = EXCLUDED.departments,
                floors = EXCLUDED.floors, is_active = TRUE, updated_at = NOW()
            RETURNING id
            """,
            (uid, a.hotel, a.role, _csv(a.departments), _csv(a.floors)),
        )
        mid = cur.fetchone()["id"]
    print(f"membership {mid}: {a.email} is {a.role} at {a.hotel}")


def cmd_make_admin(a: argparse.Namespace) -> None:
    with db.db() as cur:
        uid = _user_id(cur, a.email)
        cur.execute("UPDATE users SET is_clefs_admin = TRUE WHERE id = %s", (uid,))
    print(f"{a.email} is now a Clefs admin")


def cmd_pair_code(a: argparse.Namespace) -> None:
    with db.db() as cur:
        hotels.get_hotel(cur, a.hotel)
        out = devices.create_pairing_code(
            cur, a.hotel, name=a.name, kind=a.kind, floors=_csv(a.floors), departments=_csv(a.departments), actor=CLI_ACTOR
        )
    print(f"pairing code {out['code']} (expires {out['expires_at']:%H:%M} UTC): {get_settings().web_base_url}{out['pair_url']}")


def cmd_set_pin(a: argparse.Namespace) -> None:
    with db.db() as cur:
        devices.set_staff_pin(cur, a.hotel, a.staff_id, a.pin, actor=CLI_ACTOR)
    print(f"PIN set for staff {a.staff_id}")


def cmd_magic_link(a: argparse.Namespace) -> None:
    with db.db() as cur:
        uid = _user_id(cur, a.email)
        token = magic.create_link(cur, user_id=uid, hotel_slug=a.hotel, purpose=a.purpose, next_path=a.next)
    print(f"{get_settings().web_base_url}{magic.link_path(token)}")


def cmd_seed_demo(a: argparse.Namespace) -> None:
    """Build or rebuild the synthetic UAE demo hotel (02 spec 9) in one
    transaction, write the planted-case manifest, then print the demo
    logins, staff PINs and a fresh device pairing code. Credentials go to
    stdout only (never the log), and only for a demo hotel."""
    from datetime import date as _date

    from clefs.seed import demo_uae

    # No --anchor: seed as of the real clock (live_anchor), so a daytime run
    # never plants requests, inquiries or a brief after "now" (QA OWN-08,
    # GS-3). --anchor keeps the deterministic anchor evening (21:00 Dubai).
    anchor, as_of = (_date.fromisoformat(a.anchor), None) if a.anchor else demo_uae.live_anchor()
    password = a.password or os.environ.get("CLEFS_DEMO_PASSWORD") or None
    parts = _csv(a.parts) or None
    with db.db(statement_timeout_ms=0) as cur:
        out = demo_uae.seed(cur, a.hotel, anchor=anchor, as_of=as_of, scale=a.scale, parts=parts,
                            password=password)
    path = demo_uae.write_manifest(out, a.manifest) if not a.no_manifest else None
    # The pairing code is minted after the seed, on the real clock: codes made
    # inside the frozen anchor evening could already be expired.
    with db.db() as cur:
        pairing = devices.create_pairing_code(cur, a.hotel, name="Presenter browser (DEMO)", kind="desk",
                                              floors=[], departments=[], actor=CLI_ACTOR)
    print(f"seeded {out['hotel']} anchor={out['anchor']} as_of={out['as_of']} created={out['created']}")
    for name, counts in out["parts"].items():
        print(f"  {name}: " + ", ".join(f"{k}={v}" for k, v in counts.items() if not isinstance(v, (list, dict))))
    print(f"  plants: {len(out['plants'])}" + (f" (manifest {path})" if path else ""))
    if (out.get("wipe") or {}).get("kept"):
        print(f"  warning: rows kept in {', '.join(out['wipe']['kept'])} (delete refused)", file=sys.stderr)
    creds = out.get("credentials") or {}
    if creds.get("users"):
        print("demo logins (password shown once; re-run to rotate):")
        for role, u in creds["users"].items():
            print(f"  {role:<11} {u['email']:<40} {u['password']}")
    if creds.get("staff_pins"):
        print("staff PINs:")
        for name, pin in creds["staff_pins"].items():
            print(f"  {pin}  {name}")
    if creds.get("guest_links"):
        # Tokens are bearer links (stdout only, like the logins).
        print("guest pages (a sent J1 preferences link and a J7 feedback link):")
        for purpose, url in creds["guest_links"].items():
            print(f"  {purpose:<9} {url}")
    print(f"pair this browser as a device: {get_settings().web_base_url}{pairing['pair_url']} "
          f"(code {pairing['code']}, expires {pairing['expires_at']:%H:%M} UTC)")


def cmd_demo_present(a: argparse.Namespace) -> None:
    """Show the demo hotel under a prospect's name for one meeting: only the
    display name (and area) change, every figure stays the labelled sample.
    Refuses any hotel that is not a demo hotel."""
    with db.db() as cur:
        cur.execute("SELECT status FROM hotels WHERE slug = %s", (a.hotel,))
        row = cur.fetchone()
        if row is None or row["status"] != "demo":
            raise SystemExit(f"{a.hotel} is not a demo hotel; nothing changed")
        cur.execute("UPDATE hotels SET name = %s, city = COALESCE(%s, city) WHERE slug = %s",
                    (a.name.strip(), a.city, a.hotel))
    print(f"{a.hotel} now shows as {a.name.strip()!r}" + (f" in {a.city}" if a.city else ""))


def cmd_demo_check(a: argparse.Namespace) -> None:
    """Does the seeded demo hotel show what 02 spec 9 promises? Runs every
    per-hotel job once at the anchor evening (offline, rolled back unless
    --commit), then prints a table per module: plants found, decoys fired,
    signals by section, the brief's top 5 and the verified value. Exit 1 on
    any miss (seed/demo_uae/check.py has the details)."""
    from datetime import date as _date

    from clefs.seed.demo_uae import check

    anchor = _date.fromisoformat(a.anchor) if a.anchor else None
    rep = check.check_hotel(a.hotel, commit=a.commit, manifest_path=a.manifest, anchor=anchor, scale=a.scale,
                            jobs=not a.no_jobs)
    print(check.render(rep))
    if not rep.ok:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m clefs.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-hotel")
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--country", default="AE")
    p.add_argument("--emirate", default="dubai")
    p.add_argument("--tz", default="Asia/Dubai")
    p.add_argument("--currency", default="AED")
    p.add_argument("--locale", default="en-AE")
    p.add_argument("--city")
    p.add_argument("--star-rating", type=int)
    p.add_argument("--rooms-count", type=int)
    p.add_argument("--status", default="active", choices=["active", "pilot", "demo", "archived"])
    p.set_defaults(fn=cmd_create_hotel)

    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--phone")
    p.add_argument("--password-prompt", action="store_true")
    p.add_argument("--password-env", help="read the password from this environment variable")
    p.set_defaults(fn=cmd_create_user)

    p = sub.add_parser("grant")
    p.add_argument("--email", required=True)
    p.add_argument("--hotel", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--departments", help="comma separated")
    p.add_argument("--floors", help="comma separated")
    p.set_defaults(fn=cmd_grant)

    p = sub.add_parser("make-admin")
    p.add_argument("--email", required=True)
    p.set_defaults(fn=cmd_make_admin)

    p = sub.add_parser("pair-code")
    p.add_argument("--hotel", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--kind", default="floor", choices=list(devices.DEVICE_KINDS))
    p.add_argument("--floors")
    p.add_argument("--departments")
    p.set_defaults(fn=cmd_pair_code)

    p = sub.add_parser("set-pin")
    p.add_argument("--hotel", required=True)
    p.add_argument("--staff-id", type=int, required=True)
    p.add_argument("--pin", required=True)
    p.set_defaults(fn=cmd_set_pin)

    p = sub.add_parser("magic-link")
    p.add_argument("--email", required=True)
    p.add_argument("--hotel", required=True)
    p.add_argument("--purpose", default="brief", choices=list(magic.PURPOSES))
    p.add_argument("--next")
    p.set_defaults(fn=cmd_magic_link)

    p = sub.add_parser("seed-demo")
    p.add_argument("--hotel", default="demo-deira-crest")
    p.add_argument("--anchor", help="last history day, YYYY-MM-DD, seeded as of 21:00 Dubai (default: live, "
                                    "today as of now in Asia/Dubai; before 09:00, last night)")
    p.add_argument("--scale", type=float, default=1.0, help="reservation volume multiplier (tests use 0.1)")
    p.add_argument("--parts", help="comma separated part names (default: all)")
    p.add_argument("--password", help="one password for every demo user (else CLEFS_DEMO_PASSWORD, else random)")
    p.add_argument("--manifest", help="where to write expected_plants.json (default: next to the seed package)")
    p.add_argument("--no-manifest", action="store_true")
    p.set_defaults(fn=cmd_seed_demo)

    p = sub.add_parser("demo-present")
    p.add_argument("--hotel", default="demo-deira-crest")
    p.add_argument("--name", required=True, help='e.g. "Ramee Rose Hotel"; "Al Rigga Crest Hotel" restores it')
    p.add_argument("--city", help='the area shown with it, e.g. "Al Barsha"')
    p.set_defaults(fn=cmd_demo_present)

    p = sub.add_parser("demo-check")
    p.add_argument("--hotel", default="demo-deira-crest")
    p.add_argument("--manifest", help="expected_plants.json to check against (default: next to the seed package)")
    p.add_argument("--anchor", help="YYYY-MM-DD (default: the manifest's anchor, else the last derived day)")
    p.add_argument("--scale", type=float, default=1.0,
                   help="the --scale the hotel was seeded with; below 1 the volume checks are skipped")
    p.add_argument("--no-jobs", action="store_true", help="check the rows as they are, without running jobs")
    p.add_argument("--commit", action="store_true", help="keep what the jobs wrote (default: roll back)")
    p.set_defaults(fn=cmd_demo_check)

    args = ap.parse_args(argv)
    print(f"[clefs] database {_redact(get_settings().database_url)}", file=sys.stderr)
    try:
        args.fn(args)
    except ClefsError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
