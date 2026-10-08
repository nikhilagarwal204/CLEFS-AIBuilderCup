> **Note (Oct 2026):** Clefs now runs on Google Gemini: the Gemini API (google-genai SDK) for text, Grounding with Google Search for the AI answer radar, and the Gemini Live API for voice. The third-party findings below are kept as dated research, not as the current stack.

> Research report (UAE), gathered during planning 2026-09-27. Items marked UNVERIFIED were not confirmed against a primary source.

# In-room phone (PBX) to AI voice agent for UAE hotels: research report (2026-09-27)

## 1. Voice AI endpoint

**Endpoint:** the Gemini Live API, reached through a SIP-to-WebSocket media bridge (Twilio Media Streams, Asterisk ARI externalMedia/AudioSocket, or a SIP trunk provider's media streaming).

**Call flow:** hotel PBX → SIP trunk / media bridge → Clefs webhook → Clefs worker bridges the call audio to Gemini Live.

The SIP `From` user part is where the room extension (for example `1204`) arrives. Map it to a room through an allowlist, and never trust it for billing or authentication.

## 2. Hosting

- **Incoming-call webhook:** this is a stateless, short HTTP request. **Vercel Python serverless is fine** for it.
- **Audio bridge to Gemini Live:** this needs a process that lives as long as the call.
  - Vercel Functions (Fluid compute) can hold connections, but only up to the function's max duration: Hobby 300 s, Pro/Enterprise 300 s by default and 800 s max, and 1800 s in beta (not with Static IPs or Secure Compute). A call longer than the cap loses its tool handling. (https://vercel.com/docs/functions/limitations)
  - Vercel's WebSocket docs cover inbound WebSocket servers (Python ASGI/WSGI is supported). Holding an outbound socket inside a function is just an open connection under the same duration cap. (https://vercel.com/docs/functions/websockets)
- **Recommendation:** run a small persistent worker (FastAPI + `websockets`) on Render, Railway or Fly that holds the Gemini Live session for each call, plus transcript capture and DTMF; one instance handles many calls. Keep the webhook and the MCP tool endpoint as stateless HTTP.
- **UAE regions:**
  - Vercel `dxb1` runs on AWS me-central-1. As of 2026-09-03 it was "under maintenance and cannot be used as a target region". (https://community.vercel.com/t/dxb1-dubai-region-unavailable-maintenance-eta-and-hobby-availability/48770 ; https://vercel.com/changelog/introducing-the-dubai-vercel-region-dxb1)
  - AWS me-central-1 lost two of three AZs after drone strikes on 2026-03-01, about 17 h of outage. (https://www.datacenterdynamics.com/en/news/aws-uae-outage-after-objects-struck-the-data-center-cause-fire-amid-iran-attacks/ ; https://n2ws.com/blog/uae-aws-outage)
  - Fly.io has no Dubai region; the closest are `bom`, `fra` and `jnb`. (https://fly.io/docs/reference/regions/)
  - Place the worker close to the Gemini Live endpoint, because every audio frame passes through it.

## 3. Getting SIP out of the hotel

**Common PBXs in UAE independent hotels** (from local reseller listings; market share is **UNVERIFIED**):
- Panasonic KX-NS500/700/1000 and the older KX-TDA/TDE
- NEC SL2100 and SV9100 (the SV9100 is sold for hotels)
- Alcatel-Lucent OXO Connect
- Avaya, Grandstream UCM, 3CX
- Matrix hospitality PBXs (10–400 rooms)

Sources: https://www.pbxdubai.com/nec-pabx-system-dubai/ ; https://www.ippbxdubai.com/panasonic-pbx-uae.html ; https://www.epabxsystem.net/ip-pbx-system-supplier.html

**Routing patterns (dial "9" or "0" to reach the AI):**
1. **IP PBX that supports TLS and SRTP trunks** (Grandstream UCM6300, 3CX):
   - Create a peer VoIP trunk to the media bridge (or the SIP trunk provider's media streaming) with transport TLS and SRTP set to forced.
   - Add an outbound route or a feature code "9" that sends the call to that trunk.
   - UCM SRTP defaults to "Disabled"; the trunk setting defaults to "No". (https://www.grandstream.com/hubfs/Product_Documentation/ucm6xxx_sip_trunk_guide.pdf)
   - A UCM TLS trunk has been reported to return a 406 error. (https://forums.grandstream.com/t/ucm-sip-trunk-change-to-tls-issue/57235)
   - 3CX has native AI-agent docs at https://www.3cx.com/docs/ai-agent-configuration/ (returned 403; details **UNVERIFIED**).
2. **Legacy IP PBX** (Panasonic NS, NEC SL, OXO): NS700/1000 guides mention SIP-TLS, but SRTP support is **UNVERIFIED**. (https://www.psn-web.net/cs-im/Global/PBX/pbx/ns_nsx/manual/SIP_Trunk/Germany/NS700%20SIP%20trunk%20Setup%20Guidance%20for%20gnTel%20v1_0%20.pdf) **Recommended:** a small on-prem **bridge/SBC**, either Asterisk or FreeSWITCH on a mini-PC, or a Grandstream UCM:
   - It registers to the PBX as a SIP extension or trunk using plain UDP and G.711 on the LAN.
   - It re-originates the call to the media bridge / Gemini Live over an encrypted link.
   - It can add a shared-secret header, normalise the caller ID to the room number, and take REFERs back to the front desk.
3. **Analog or hybrid PBX:**
   - Plug an **FXO gateway** into a spare analog extension port: Grandstream HT813 (1 FXO, 1 call) or GXW4104/4108 (4 or 8 FXO, 4 or 8 calls).
   - Set the PBX so that "9" rings that port. Set the gateway's "Unconditional Call Forward to VOIP" to the bridge.
   - Both devices support TLS and SRTP.
   - Caveat: through FXO the caller ID becomes the port and not the room, unless the PBX passes calling-party ID. Mitigation is to ask for the room number in the dialogue, or check against the PMS.
   - Sources: https://www.grandstream.com/products/gateways-and-atas/analog-telephone-adaptors/product/ht813 ; https://www.grandstream.com/hubfs/Product_Documentation/datasheet_gxw410x_english.pdf ; https://www.grandstream.com/hubfs/Product_Documentation/HT813_Administration_Guide.pdf

**Firewall:** allow outbound traffic from the on-prem bridge to the media bridge / Clefs worker, on the ports that bridge uses.

**UAE regulation (not legal advice):**
- TDRA's VoIP Regulatory Policy (v2.0, 30 Dec 2009) treats any voice over IP as a telecom service. Only licensees (e&, du), parties working with them, or TDRA-approved providers may provide it. Licensees may offer VoIP "for internal communications" as a feature on top of connectivity. Traffic with "network breakout" (to the PSTN) is only allowed through du or e&. (https://tdra.gov.ae/en/FAQs ; https://tdra.gov.ae/-/media/About/regulations-and-ruling/EN/Voice-over-internet-protocol-Regulatory-policy-pdf.ashx)
- Guest extension → media bridge / Gemini Live has no PSTN breakout, and is closer to an enterprise app than a telecom service. Whether it is permitted is **UNVERIFIED / grey area**.
- Mitigations:
  - Never enable `refer` to `tel:` numbers.
  - Run the link over the hotel's e&/du business connection.
  - Get written confirmation from the e&/du enterprise account manager or TDRA.
  - Consider an e&/du SIP trunk offering.
- Non-compliant VoIP "is actively blocked at the ISP level". SIP on 5061 might be DPI-blocked; that is **UNVERIFIED** for business lines and must be tested on site. (https://ethic-it.com/sbc-ucaas-uae-gcc/)
- Technical fallback (check the regulatory side first): the bridge streams G.711 over a WebSocket on port 443, using Asterisk ARI externalMedia or AudioSocket feeding the Clefs worker, which bridges to Gemini Live.

**Twilio in the UAE:**
- Geographic numbers are **inbound-only**, with outbound "strictly prohibited… via… SIP Trunk". Toll-free numbers must terminate in the UAE. No emergency calling. (https://www.twilio.com/en-us/guidelines/ae/voice)
- The SIP trunking pricing page says "we don't have voice enabled numbers in this locale", which conflicts with the guidelines page.
- Termination costs $0.2955/min to mobile and $0.3595/min to landline. (https://www.twilio.com/en-us/sip-trunking/pricing/ae)
- Twilio is not needed for in-house extensions.

## 4. WebRTC in the guest browser on e& / du

- The UAE blocks consumer VoIP (WhatsApp, FaceTime audio, Skype) using DPI. Teams, Zoom, Google Meet and Webex are cleared, and BOTIM runs on the operator "Internet Calling Plan". (https://simology.io/blog/uae-voip-app-calling-what-works-what-doesnt-2026-traveller-guide ; https://gulfnews.com/technology/yep-skypes-blocked-in-the-uae-what-now-1.2150373)
- **Mitigations:**
  - Probe the connection on load: if ICE fails within about 5 s, fall back to a WebSocket audio relay through the Clefs worker, or to text chat.
  - Test on e& mobile, du mobile and hotel Wi-Fi before launch.

## 5. Alternatives

| Option | How it works | Cost | Pros | Cons |
|---|---|---|---|---|
| **Gemini Live via media bridge** | PBX/bridge → SIP-to-WebSocket media bridge (Twilio Media Streams, Asterisk ARI externalMedia/AudioSocket, or a SIP trunk provider's media streaming) → Clefs worker → Gemini Live | Bridge or trunk plus Gemini Live usage | Clefs owns the audio path, tools and transcripts | Needs a persistent worker; one extra hop |
| **Twilio Voice + Media Streams** | Twilio number → `<Connect><Stream>` WS to your server → Gemini Live | Twilio per-minute plus tokens | Mature, recordings | UAE numbers restricted, needs a persistent WS server, extra hop |
| **LiveKit SIP + Agents** | BYO SIP trunk → LiveKit room → Python agent | Agent $0.01/min after included minutes; third-party SIP $0.004/min (Ship) or $0.003/min (Scale); Build tier 5 concurrent, Ship 20 (https://livekit.com/pricing) | Open source and self-hostable, any STT/LLM/TTS, fine-grained control | You run the agent worker; no Middle East region listed |
| **Vapi** | BYO SIP or provider | $0.05/min platform fee plus providers; about $0.10–0.30 all-in (https://www.cloudtalk.io/blog/vapi-ai-pricing/) | Fast to build, tools via webhooks (serverless-friendly) | Vendor lock-in, data leaves your stack |
| **Retell** | BYO SIP (free telephony leg) | About $0.055/min infrastructure plus LLM/TTS; $0.07–0.31/min (https://www.cloudtalk.io/retell-ai-pricing/) | Good turn-taking, webhook tools | Same lock-in; Arabic quality **UNVERIFIED** |

## Recommendation for the Clefs plan

1. **Entry:** the SIP trunk or media bridge (the on-prem bridge below, Twilio Media Streams, or a SIP trunk provider's media streaming) calls the Clefs webhook `/webhooks/voice/incoming`, signed with HMAC-SHA256 in the `X-Clefs-Signature` header using `VOICE_WEBHOOK_SECRET`. Clefs verifies the signature and takes the room from the `From` header against the `hotel_extensions` allowlist.
2. **Audio:** the Clefs worker (Render or Railway) bridges the call audio to the Gemini Live API with hotel-specific instructions, and captures transcripts and DTMF.
3. **Tools:** `create_ticket`, `get_hotel_info` and `transfer_to_front_desk`, served from the Clefs MCP endpoint `/webhooks/voice/mcp`.
4. **Hardware SKU:** an on-prem "Clefs Voice Bridge" (Asterisk mini-PC or Grandstream UCM, plus an HT813/GXW410x for analog PBXs) handling TLS/SRTP and room caller ID.
5. **Regulatory:** disable PSTN `refer` targets, and get written e&/du sign-off before going live.
