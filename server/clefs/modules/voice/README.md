# In-room phone AI (`voice`)

Owned by lane **B Concierge + Voice** (part "voice"). Only that lane edits this directory (plus `tests/modules/voice/`). Spec: `docs/specs/03-service-guest-trust-marketing.md` section 3.

- Migrations: `migrations/NNNN_voice_<desc>.sql`, numbers 0850-0899. Tables start with `vc_`: `vc_lines`, `vc_extensions`, `vc_calls`, `vc_webhook_receipts`, `vc_tool_calls` (0850).
- Permissions: `voice.view` (owner, gm, front_desk), `voice.manage` (owner, gm).
- `api.py` is the public surface (`call_stats`). Other modules import only `clefs.modules.voice.api`.

## Files

| File | What |
|---|---|
| `webhook.py` | `POST /webhooks/voice/incoming`: X-Clefs-Signature HMAC-SHA256 over the raw body (VOICE_WEBHOOK_SECRET), dedupe on `vc_webhook_receipts`, line identification (X-Clefs-Line secret, else `allowed_sources` on a line of the dialled SIP trunk; source hosts are caller-written, so a source-identified call never binds its From extension as a verified room and counts rate limits in its own bucket), SIP 486 for anything unidentified, disabled, over capacity, after hours, rate limited or whose concierge is not live (`concierge.api.is_live`, the readiness gate; also when `concierge.api.open_session` then refuses the phone mint with `concierge_not_live`, so a switch-off racing the call is never answered without a session), extension -> room allowlist (header-identified calls only), `concierge.api.build_surface_session(surface="phone")` and `open_session(transport="sip")`, accept inline |
| `lines.py` | lines CRUD, SIP header parsing, identification and admission, the extension allowlist (single, CSV, "extension = room" rule) |
| `calls.py` | per-call JWT (HS256, key derived from SECRET_KEY), the phone prompt block (03 spec 3.5), the accept payload (MCP tool with the JWT, or sideband function tools) |
| `realtime_ops.py` | accept / reject / refer / hangup through one swappable transport (SDK by default, `Recorder` in tests; honours VOICE_BRIDGE_URL) |
| `mcp_server.py` | `POST /webhooks/voice/mcp`: MCP Streamable HTTP, stateless JSON responses; the JWT is the only auth, claims bind hotel, call and extension room |
| `executor.py` | one entry for MCP and sideband tools: room binding (JWT room wins; spoken room sticky once confirmed against the register), the `vc_tool_calls` idempotency ledger, sip:/sips:-only REFER with a callback ticket on failure. Every other tool runs on the concierge executor through `concierge.api.run_tool(cur, hotel_slug, session_ctx={session_id: the call's cc_sessions id, call_id: the ledger key}, name, args)` (03 spec 0.3, one executor for every surface): `create_ticket` becomes `notify_staff` (with `label` from `request_type`), `get_hotel_info` becomes `search_hotel_info`. `concierge_available(cur, call)` decides: the call has a bound session and `hotels.module_enabled(cur, slug, 'concierge')` |
| `fallback.py` | guarded fallback only: the voice-local tool executor over `service.api`, `upsell.api` and `concierge.api.kb_facts`, used for a call the concierge cannot serve (the concierge module switched off for the hotel or absent from the build, or no concierge session bound when the call was answered); logged at info per call. Same tool contract. Orders go through upsell's session path (`quote_for_session` then `confirm_order`, session `vc:<call id>`) with `verified_room` true only for an extension-bound room, so a spoken room keeps its door check and the hotel's `allow_unverified_rooms` setting applies. `get_request_status` widens to the room's other calls (36 h) only for an extension-bound room; a spoken-room call sees only its own requests. |
| `worker.py` | `voice_sideband` (a `Module.worker_services` entry: lease, transcripts to `conversation_logs` source `phone`, safety injection, sideband function tools, end on socket close) and the jobs `voice.lease_reaper` (5 min) and `voice.receipts_purge` (daily, 30 days) |
| `router.py` | console under `/hotels/{slug}/voice/`: status, lines, extensions (CSV and rule), webhook event log, call log with transcripts masked by role |
| `harness.py` | `python -m clefs.modules.voice.harness`: local telephony-bridge stub, signed `call.incoming`, MCP `tools/call` with the minted JWT, `--eval` text mode |

## Harness

```
cd server
DATABASE_URL=postgresql://localhost/clefs_dev .venv/bin/python -m clefs.modules.voice.harness \
  --hotel <slug> --setup --from 1501 --room 501 --tool create_ticket \
  --args '{"request_type": "towels", "description_en": "2 towels"}' --replay
```

`--no-line-header --from-host stranger.example` shows the 486 path; `--fail accept` the accept-failure fallback; `--transport sideband` a sideband line; `--api http://localhost:8000 --webhook-secret whsec_... --stub-port 9911` posts to a running server started with `VOICE_BRIDGE_URL=http://127.0.0.1:9911/v1`. The harness refuses a non-local DATABASE_URL.

## Build-time checks still open (Canon 15)

- The Gemini Live media bridge (worker.py) has not been tested against a live carrier: `tool_transport` is per line, and the harness covers both transports.
- Call lifecycle: the bridge types only `call.incoming` (and `call.initiated`). Any event whose type names an end closes the call row; otherwise the worker's socket close records the end, and without either the reaper marks the call `stale` with `ended_at` NULL.
- DTMF: logged as a system turn when an event type contains `dtmf`.
