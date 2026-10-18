"""
The phone harness (03 spec 3.6): python -m clefs.modules.voice.harness

Exercises the whole SIP path without a PBX, a telephony bridge or Gemini:
  1. starts a local telephony-bridge stub that records accept/reject/refer/hangup
     payloads, and points VOICE_BRIDGE_URL at it (realtime_ops honours it);
  2. signs a fake call.incoming (X-Clefs-Signature, HMAC-SHA256 of the raw
     body with VOICE_WEBHOOK_SECRET) with a chosen From and headers, posts it to the
     webhook (in-process against the local DB, or --api at a running server
     that was started with VOICE_BRIDGE_URL=<the stub>, see --stub-port);
  3. calls the MCP server (initialize, tools/list, tools/call) with the
     per-call JWT the stub captured from the accept payload;
  4. optionally runs the phone prompt through the text runner (--eval).

It prints one JSON report. --setup creates or reuses an enabled "harness"
line for --hotel and maps --from to --room, so a fresh dev DB works.

Local databases only: the harness refuses a non-local DATABASE_URL.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
OP_RE = re.compile(r"/calls/(?P<call>[^/]+)/(?P<op>accept|reject|refer|hangup)$")


# ─── Telephony bridge stub ───


class StubBridge:
    """A tiny HTTP server answering POST /v1/calls/{id}/{op} with
    200 and recording (op, call_id, body). fail_ops makes an op answer 500."""

    def __init__(self, port: int = 0, fail_ops: set[str] | None = None) -> None:
        self.records: list[dict] = []
        self.fail_ops = set(fail_ops or ())
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:  # quiet
                return

            def do_POST(self) -> None:  # noqa: N802 (http.server API)
                size = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(size) if size else b""
                m = OP_RE.search(self.path.split("?")[0])
                if not m:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"_raw": raw.decode(errors="replace")}
                stub.records.append({"op": m["op"], "call_id": m["call"], "body": body})
                code = 500 if m["op"] in stub.fail_ops else 200
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}" if code == 200 else b'{"error": {"message": "stub failure"}}')

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="voice-stub", daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def start(self) -> "StubBridge":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def ops(self, op: str) -> list[dict]:
        return [r for r in self.records if r["op"] == op]


# ─── Signing ───


def new_webhook_secret() -> str:
    return "vwh_" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def sign(body: bytes, secret: str, *, webhook_id: str | None = None) -> dict[str, str]:
    """X-Clefs-Webhook-Id, X-Clefs-Timestamp and X-Clefs-Signature, as the bridge sends them."""
    from clefs.modules.voice import realtime_ops

    wid = webhook_id or "msg_" + uuid.uuid4().hex
    ts = str(int(time.time()))
    return {"X-Clefs-Webhook-Id": wid, "X-Clefs-Timestamp": ts, "content-type": "application/json",
            "X-Clefs-Signature": realtime_ops.signature(body, secret, webhook_id=wid, timestamp=ts)}


def incoming_event(*, call_id: str, from_user: str | None, from_host: str, project: str,
                   headers: list[tuple[str, str]] = ()) -> dict:
    sip = [
        {"name": "From", "value": f"<sip:{from_user}@{from_host}>;tag={uuid.uuid4().hex[:8]}" if from_user
         else f"<sip:{from_host}>"},
        {"name": "To", "value": f"<sip:{project}@sip.voice.clefs.app;transport=tls>"},
        {"name": "Via", "value": f"SIP/2.0/TLS {from_host}:5061;branch=z9hG4bK{uuid.uuid4().hex[:8]}"},
        {"name": "Call-ID", "value": uuid.uuid4().hex},
    ]
    sip += [{"name": k, "value": v} for k, v in headers]
    return {"id": "evt_" + uuid.uuid4().hex[:16], "object": "event", "type": "call.incoming",
            "created_at": int(time.time()), "data": {"call_id": call_id, "sip_headers": sip}}


# ─── Targets ───


class Target:
    """post(path, *, content|json, headers) -> (status, json). In-process
    uses FastAPI's TestClient on create_app(); --api uses httpx."""

    def __init__(self, api: str | None) -> None:
        self.api = api.rstrip("/") if api else None
        if self.api:
            import httpx

            self._http = httpx.Client(base_url=self.api, timeout=15.0)
        else:
            from fastapi.testclient import TestClient

            from clefs.app import create_app

            self._http = TestClient(create_app())

    def post(self, path: str, *, content: bytes | None = None, json_body: Any = None,
             headers: dict | None = None) -> tuple[int, Any]:
        if json_body is not None:
            r = self._http.post(path, json=json_body, headers=headers or {})
        else:
            r = self._http.post(path, content=content, headers=headers or {})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text


def ensure_local_db() -> None:
    """The effective URL (DATABASE_URL, else what settings resolve, which
    may be server/.env's production PSQL_DATABASE_URL) must be local."""
    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        try:
            from clefs.settings import get_settings

            url = get_settings().database_url
        except Exception as exc:
            raise SystemExit(f"no usable local database: {exc}") from None
    host = urlparse(url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing to run against a non-local database ({host}); set DATABASE_URL to a local one")


def setup_line(hotel: str, *, transport: str, from_user: str | None, room: str | None, front_desk: str) -> dict:
    """Create or reuse the hotel's "harness" line (enabled, the chosen
    transport) and map from_user -> room. Returns {line_id, secret, project}."""
    from clefs import db
    from clefs.modules.voice import lines

    with db.db() as cur:
        cur.execute("SELECT * FROM vc_lines WHERE hotel_slug = %s AND label = 'harness' ORDER BY id LIMIT 1", (hotel,))
        row = cur.fetchone()
        values = {"enabled": True, "tool_transport": transport, "front_desk_uri": front_desk, "max_concurrent": 50}
        if row is None:
            row, secret = lines.create_line(cur, hotel, {"label": "harness", "trunk_id": "proj_harness",
                                                         "pbx_kind": "other", **values})
        else:
            lines.update_line(cur, hotel, row["id"], values)
            row, secret = lines.rotate_secret(cur, hotel, row["id"])
        if from_user and room:
            lines.add_extension(cur, hotel, row["id"], {"extension": from_user, "room": room})
    return {"line_id": row["id"], "secret": secret, "project": row.get("trunk_id") or "proj_harness"}


# ─── MCP ───


def mcp_session(target: Target, token: str, tool: str | None, args: dict) -> dict:
    """initialize, tools/list and (optionally) one tools/call."""
    hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    out: dict[str, Any] = {}

    def rpc(method: str, params: dict, msg_id: int) -> Any:
        status, body = target.post("/webhooks/voice/mcp", json_body={"jsonrpc": "2.0", "id": msg_id, "method": method,
                                                                      "params": params}, headers=hdrs)
        return {"status": status, "body": body}

    out["initialize"] = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "clefs-harness", "version": "1"}}, 1)
    listed = rpc("tools/list", {}, 2)
    out["tools"] = [t["name"] for t in ((listed["body"] or {}).get("result") or {}).get("tools", [])] \
        if isinstance(listed["body"], dict) else listed
    if tool:
        out["call"] = rpc("tools/call", {"name": tool, "arguments": args}, 3)
    return out


# ─── Eval (text mode) ───


def eval_text(hotel: str, *, room: str | None, language: str, text: str) -> dict:
    """The phone prompt through concierge's text runner (respond_text)."""
    from clefs.modules.concierge import api as concierge_api

    spec = concierge_api.build_surface_session(hotel, surface="phone", room=room, language=language, verified=bool(room))
    turn = concierge_api.respond_text(spec, [{"role": "user", "content": text}])
    return {"prompt_version": spec.prompt_version, "reply": turn.text, "tool_calls": turn.tool_calls}


# ─── Main ───


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m clefs.modules.voice.harness", description=__doc__.split("\n\n")[0])
    p.add_argument("--hotel", help="hotel slug (needed for --setup and --eval)")
    p.add_argument("--setup", action="store_true", help="create/reuse an enabled 'harness' line and map --from to --room")
    p.add_argument("--room", help="room to map --from to (with --setup), and the room for --eval")
    p.add_argument("--from", dest="from_user", default="1501", help="SIP From user part (the extension); '' for none")
    p.add_argument("--from-host", default="pbx.harness.local")
    p.add_argument("--project", default=None, help="SIP trunk id in the To header")
    p.add_argument("--line-secret", help="X-Clefs-Line value (default: the --setup line's)")
    p.add_argument("--no-line-header", action="store_true", help="omit X-Clefs-Line (test allowed_sources or a 486)")
    p.add_argument("--header", action="append", default=[], help="extra SIP header NAME=VALUE (repeatable)")
    p.add_argument("--transport", choices=("mcp", "sideband"), default="mcp")
    p.add_argument("--front-desk", default="sip:frontdesk@pbx.harness.local")
    p.add_argument("--webhook-secret", default=None, help="VOICE_WEBHOOK_SECRET (default: env or a generated one in-process)")
    p.add_argument("--api", help="post to a running API (start it with VOICE_BRIDGE_URL=http://127.0.0.1:<stub-port>/v1)")
    p.add_argument("--stub-port", type=int, default=0)
    p.add_argument("--fail", action="append", default=[], choices=("accept", "reject", "refer", "hangup"),
                   help="make the stub fail this op (repeatable)")
    p.add_argument("--replay", action="store_true", help="deliver the same webhook twice (idempotency)")
    p.add_argument("--tool", help="MCP tool to call, e.g. create_ticket or transfer_to_front_desk")
    p.add_argument("--args", default="{}", help="JSON arguments for --tool")
    p.add_argument("--eval", dest="eval_text", help="run the phone prompt on this caller text (text mode)")
    p.add_argument("--language", default="en")
    return p.parse_args(argv)


def _configure_env(ns: argparse.Namespace, stub: StubBridge) -> str:
    os.environ["VOICE_BRIDGE_URL"] = stub.base_url
    os.environ.setdefault("GEMINI_API_KEY", "harness-key")
    secret = ns.webhook_secret or os.environ.get("VOICE_WEBHOOK_SECRET")
    if not secret:
        if ns.api:
            raise SystemExit("--api needs --webhook-secret (the one the running API has)")
        secret = new_webhook_secret()
    if not ns.api:
        os.environ["VOICE_WEBHOOK_SECRET"] = secret
        os.environ["VOICE_ENABLED"] = "true"
    from clefs.settings import reload_settings

    reload_settings()
    return secret


def run(argv: list[str] | None = None) -> dict:
    ns = _parse(argv)
    if not ns.api or ns.setup or ns.eval_text:
        ensure_local_db()
    stub = StubBridge(ns.stub_port, set(ns.fail)).start()
    report: dict[str, Any] = {"stub": stub.base_url}
    try:
        secret = _configure_env(ns, stub)
        project, line_secret = ns.project, ns.line_secret
        if ns.setup:
            if not ns.hotel:
                raise SystemExit("--setup needs --hotel")
            made = setup_line(ns.hotel, transport=ns.transport, from_user=ns.from_user or None, room=ns.room,
                              front_desk=ns.front_desk)
            report["line"] = {"id": made["line_id"], "secret": made["secret"]}
            project, line_secret = project or made["project"], line_secret or made["secret"]
        extra = [tuple(h.split("=", 1)) for h in ns.header if "=" in h]
        if line_secret and not ns.no_line_header:
            extra.append(("X-Clefs-Line", line_secret))
        call_id = "rtc_harness_" + uuid.uuid4().hex[:12]
        event = incoming_event(call_id=call_id, from_user=ns.from_user or None, from_host=ns.from_host,
                               project=project or "proj_harness", headers=extra)
        body = json.dumps(event).encode()
        hdrs = sign(body, secret)
        target = Target(ns.api)
        deliveries = [target.post("/webhooks/voice/incoming", content=body, headers=hdrs)]
        if ns.replay:
            deliveries.append(target.post("/webhooks/voice/incoming", content=body, headers=hdrs))
        report["call_id"] = call_id
        report["webhook"] = [{"status": s, "body": b} for s, b in deliveries]
        accepts = [r for r in stub.ops("accept") if r["call_id"] == call_id]
        token = None
        if accepts:
            token = (accepts[0]["body"].get("mcp") or {}).get("authorization")
        if token:
            report["mcp"] = mcp_session(target, token, ns.tool, json.loads(ns.args or "{}"))
        if ns.eval_text:
            if not ns.hotel:
                raise SystemExit("--eval needs --hotel")
            report["eval"] = eval_text(ns.hotel, room=ns.room, language=ns.language, text=ns.eval_text)
        report["bridge"] = [{**r, "body": _redact(r["body"])} for r in stub.records]
    finally:
        stub.stop()
    return report


def _redact(body: dict) -> dict:
    """Shorten the prompt and the JWT in the printed report."""
    out = dict(body)
    if isinstance(out.get("instructions"), str) and len(out["instructions"]) > 400:
        out["instructions"] = out["instructions"][:400] + f"... ({len(body['instructions'])} chars)"
    if isinstance(out.get("mcp"), dict) and out["mcp"].get("authorization"):
        out["mcp"] = {**out["mcp"], "authorization": out["mcp"]["authorization"][:16] + "..."}
    if isinstance(out.get("tools"), list):
        out["tools"] = [{"type": t.get("type"), "name": t.get("name") or (t.get("function") or {}).get("name")}
                        for t in out["tools"] if isinstance(t, dict)]
    return out


def main(argv: list[str] | None = None) -> int:
    report = run(argv)
    print(json.dumps(report, indent=2, default=str))
    return 0 if all(d["status"] == 200 for d in report.get("webhook", [])) else 1


if __name__ == "__main__":
    sys.exit(main())
