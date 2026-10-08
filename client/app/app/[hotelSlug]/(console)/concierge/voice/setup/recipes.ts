/* ═══════════════════════════════════════════════════════════════════════
   PBX recipes for the in-room phone AI (03 spec 3.6 step 4). Data only:
   the setup page renders them. Placeholders in snippets are filled from the
   chosen line: {SIP_URI}, {PROJECT_ID}, {SECRET} (never known after
   creation, so it stays a placeholder the engineer pastes), {AI_EXT}.

   Common to every PBX (the telephony bridge's SIP interface):
     - signalling SIP over TLS on port 5061 (no plain UDP 5060);
     - SRTP mandatory (SDES keys in the SDP);
     - G.711 only: PCMU (mu-law) or PCMA (A-law); UAE PBXs usually A-law;
     - outbound TCP 5061 and UDP for SRTP media to the telephony bridge's published SIP
       ranges; nothing inbound needs opening.
   Regulatory: internal calls only. The AI never dials out (no PSTN through
   the telephony bridge) and transfers go to sip:/sips: extensions, never tel: numbers.
   ═══════════════════════════════════════════════════════════════════════ */

import type { PbxKind } from "../../types";

export interface Recipe {
  kind: PbxKind;
  title: string;
  summary: string;
  /** Can the PBX add the X-Clefs-Line header itself? */
  header: "native" | "bridge" | "n/a";
  steps: string[];
  snippet?: { label: string; lang: string; text: string };
  caveats?: string[];
}

export const NETWORK_REQUIREMENTS: readonly string[] = [
  "SIP over TLS to sip.voice.clefs.app on TCP 5061. Plain UDP 5060 is refused.",
  "SRTP is mandatory (SDES). A trunk offering plain RTP gets no audio.",
  "Codecs: G.711 only, PCMA (A-law, usual in the UAE) or PCMU (mu-law). Disable G.729, G.722 and Opus on this trunk.",
  "Firewall: allow outbound TCP 5061 and outbound UDP for SRTP media to the telephony bridge's published SIP address ranges. No inbound port forwarding is needed.",
  "Give the AI a short internal extension (for example 8000) and add a one-touch key or a card by the phone: \"Dial 8000 for the concierge\".",
];

export const REGULATORY: readonly string[] = [
  "Internal calls only: guests reach the AI from room phones on the hotel PBX. Do not route outside (PSTN) callers to it.",
  "The AI never places outside calls. Transfers go to internal sip: or sips: extensions (the front desk), never to tel: numbers.",
  "Before go-live, get written sign-off from the hotel's licensed telecom provider (e& or du) that the SIP trunk to the telephony bridge is used for internal extensions only.",
  "The AI says it is an AI and that the call is recorded in its first sentence. Transcripts are kept 12 months, then deleted.",
];

export const TEST_CHECKLIST: readonly { key: string; text: string }[] = [
  { key: "event", text: "Dial the AI extension from a mapped room: a call.incoming event appears below with outcome accepted." },
  { key: "greeting", text: "The AI greets in the line's opening language, says it is an AI and that the call is recorded." },
  { key: "room", text: "It confirms the room (\"That's for room 504, correct?\") before filing a request." },
  { key: "request", text: "Ask for two towels: a request appears in Service for that room, marked verified." },
  { key: "unmapped", text: "Call from an unmapped extension: the AI asks for the room, and the request is marked for staff to confirm at the door." },
  { key: "arabic", text: "Speak Arabic: it switches to Arabic and keeps going." },
  { key: "transfer", text: "Say \"front desk please\": the call transfers to the front desk extension (or a callback request is filed)." },
  { key: "emergency", text: "Say \"someone has collapsed\" (tell the front desk first): it gives the dial sequence for 998 and files an emergency request." },
  { key: "reject", text: "Switch the line off and call again: the PBX gets busy (486) and rings its fallback." },
];

const GRANDSTREAM: Recipe = {
  kind: "grandstream_ucm",
  title: "Grandstream UCM",
  summary: "A peer SIP trunk to the telephony bridge plus an outbound route on the AI extension. The UCM cannot add custom SIP headers, so identify the line by its public IP (allowed sources) or put a small SIP bridge in front.",
  header: "bridge",
  steps: [
    "Extension/Trunk > VoIP Trunks > Add: type Peer SIP Trunk, Host sip.voice.clefs.app, Port 5061, Transport TLS.",
    "In the trunk's Advanced Options: SRTP enabled (mandatory), codecs PCMA then PCMU only, Keep CID on, From User {PROJECT_ID}.",
    "Outbound Routes > Add: pattern 8000 (the AI extension), trunk = the the telephony bridge trunk, Strip 4 digits, Prepend nothing. Set the dial target user to {PROJECT_ID}.",
    "Allow the room extensions' permission level to use that route (Internal is enough: no outside dialling is involved).",
    "In Clefs, set Allowed sources on the line to the UCM's public IP (calls then always ask the guest for the room), or run the bridge, which adds X-Clefs-Line so extensions map to verified rooms.",
    "Keep caller id as the extension number so Clefs can match it to the room (the extension map).",
    "Set the route's failover to the operator extension: when Clefs answers busy (486) the call rings the operator instead.",
  ],
  caveats: [
    "UCM firmware before 1.0.20 cannot do SRTP on peer trunks: upgrade first.",
    "Without the header, anyone who spoofs the UCM's IP could reach the line. Prefer the bridge when the UCM sits behind a shared NAT.",
  ],
};

const THREECX: Recipe = {
  kind: "3cx",
  title: "3CX",
  summary: "A generic SIP trunk to the telephony bridge with a custom header, and a dial code for the AI.",
  header: "native",
  steps: [
    "SIP Trunks > Add SIP Trunk > Generic > Generic SIP Trunk (IP based). Registrar/Server sip.voice.clefs.app, port 5061, transport TLS.",
    "Authentication: none (IP based). Outbound Parameters: set Request Line URI user part to {PROJECT_ID}.",
    "Options: Secure RTP (SRTP) required; codec priority PCMA, PCMU; remove all others.",
    "Outbound Parameters > Add custom header: X-Clefs-Line = {SECRET}.",
    "Outbound Rules > Add: calls to number 8000 from extension group Rooms, route 1 = the the telephony bridge trunk, strip 4 digits.",
    "Keep the caller's extension as the From user (Outbound Caller ID: leave blank so the extension is sent).",
  ],
};

const ASTERISK: Recipe = {
  kind: "asterisk",
  title: "Asterisk / FreePBX (PJSIP)",
  summary: "A PJSIP endpoint over TLS with SDES SRTP, the X-Clefs-Line header added in the dialplan, and the caller id normalised to the room extension.",
  header: "native",
  steps: [
    "Add a TLS transport (port 5061) if the system has none, with a valid CA bundle so the the telephony bridge certificate verifies.",
    "Create the endpoint below (FreePBX: Connectivity > Trunks > Add SIP (chan_pjsip) Trunk, then the same values in the Advanced tab).",
    "Add the dialplan: extension 8000 adds X-Clefs-Line and dials the the telephony bridge endpoint.",
    "Make sure CALLERID(num) is the room's extension (strip any DID prefix) so Clefs can match it.",
  ],
  snippet: {
    label: "pjsip.conf and extensions.conf",
    lang: "ini",
    text: `; pjsip.conf
[transport-tls]
type=transport
protocol=tls
bind=0.0.0.0:5061
method=tlsv1_2
ca_list_file=/etc/ssl/certs/ca-certificates.crt

[clefsbridge]
type=endpoint
transport=transport-tls
context=from-clefsbridge
disallow=all
allow=alaw,ulaw
media_encryption=sdes
media_encryption_optimistic=no
aors=clefsbridge
from_user={PROJECT_ID}

[clefsbridge]
type=aor
contact=sip:sip.voice.clefs.app:5061\\;transport=tls

; extensions.conf
[from-rooms]
exten => 8000,1,NoOp(Guest to Clefs AI concierge)
 same => n,Set(CALLERID(num)=\${CALLERID(num):-4})   ; keep the room extension only
 same => n,Dial(PJSIP/{PROJECT_ID}@clefsbridge,,b(add-clefs-header^s^1))
 same => n,Goto(operator,s,1)                         ; 486 busy: ring the operator

[add-clefs-header]
exten => s,1,Set(PJSIP_HEADER(add,X-Clefs-Line)={SECRET})
 same => n,Return()`,
  },
  caveats: ["PJSIP_HEADER(add) must run on the outbound channel: the pre-dial handler (b option) does that."],
};

const FREESWITCH: Recipe = {
  kind: "freeswitch",
  title: "FreeSWITCH",
  summary: "An external gateway over TLS with SRTP, and a dialplan extension that sets sip_h_X-Clefs-Line before bridging.",
  header: "native",
  steps: [
    "Enable TLS on the external profile (tls=true, tls-sip-port 5061) with a CA bundle.",
    "Add the gateway below under sip_profiles/external/.",
    "Add the dialplan extension; sip_h_ variables become SIP headers on the outbound INVITE.",
    "Keep effective_caller_id_number as the room extension.",
  ],
  snippet: {
    label: "gateway and dialplan XML",
    lang: "xml",
    text: `<!-- sip_profiles/external/clefsbridge.xml -->
<include>
  <gateway name="clefsbridge">
    <param name="proxy" value="sip.voice.clefs.app:5061"/>
    <param name="register" value="false"/>
    <param name="register-transport" value="tls"/>
    <param name="caller-id-in-from" value="true"/>
  </gateway>
</include>

<!-- dialplan/default/00_clefs_ai.xml -->
<extension name="clefs_ai">
  <condition field="destination_number" expression="^8000$">
    <action application="set" data="rtp_secure_media=mandatory"/>
    <action application="set" data="absolute_codec_string=PCMA,PCMU"/>
    <action application="set" data="sip_h_X-Clefs-Line={SECRET}"/>
    <action application="set" data="continue_on_fail=USER_BUSY"/>
    <action application="bridge" data="sofia/gateway/clefsbridge/{PROJECT_ID};transport=tls"/>
    <action application="transfer" data="operator XML default"/>
  </condition>
</extension>`,
  },
};

const FXO: Recipe = {
  kind: "fxo_gateway",
  title: "Analog lines: HT813 or GXW410x FXO gateway",
  summary: "For hotels with analog room phones on an old PBX: an FXO port answers the AI extension and hands the call to a SIP bridge. Caller id is usually lost, so the AI asks for the room.",
  header: "bridge",
  steps: [
    "Connect an extension port of the analog PBX to the gateway's FXO port, and give that extension the AI number (for example 8000).",
    "On the gateway: FXO port > Stage Method 1 (one-stage dialling), Unconditional call forward to VoIP: the bridge's SIP address.",
    "Point the gateway at a SIP bridge (Asterisk or FreeSWITCH, recipes above) that adds X-Clefs-Line and dials the telephony bridge over TLS with SRTP. The HT813 and GXW410x cannot do TLS plus SRTP to the telephony bridge directly with a custom header.",
    "In Clefs, set the line's Room from caller id to No: the AI asks for the room and confirms it, and requests are marked for staff to confirm at the door.",
    "Turn on caller id pass-through (Bellcore/FSK) only if the analog PBX sends it; then map extensions as usual.",
  ],
  caveats: ["One FXO port carries one call at a time: set Calls at once on the line to the number of ports."],
};

const BRIDGE: Recipe = {
  kind: "bridge",
  title: "SIP bridge in front of any PBX",
  summary: "A small Asterisk or FreeSWITCH box between the PBX and the telephony bridge. The PBX sends plain SIP to the bridge on the LAN; the bridge adds X-Clefs-Line and speaks TLS plus SRTP to the telephony bridge.",
  header: "native",
  steps: [
    "Use the Asterisk or FreeSWITCH recipe for the bridge's the telephony bridge side.",
    "On the PBX, route the AI extension to the bridge as a LAN SIP trunk, keeping the caller's extension as the From user.",
    "Firewall the bridge so only the PBX can reach it.",
  ],
};

const OTHER: Recipe = {
  kind: "other",
  title: "Other SIP PBX",
  summary: "Any PBX that can place a SIP call over TLS with SRTP and G.711 works. If it can add a custom header, send X-Clefs-Line; if not, list its public IP under Allowed sources or use a bridge.",
  header: "n/a",
  steps: [
    "Create a SIP trunk to {SIP_URI} (TLS 5061, SRTP mandatory, PCMA/PCMU).",
    "Add header X-Clefs-Line: {SECRET} on calls to it, or add the PBX's public IP to Allowed sources.",
    "Route the AI extension to the trunk; send the caller's extension as the From user.",
    "On a busy answer (486) fail over to the operator.",
  ],
};

export const RECIPES: readonly Recipe[] = [GRANDSTREAM, THREECX, ASTERISK, FREESWITCH, FXO, BRIDGE, OTHER];

/** Fill the placeholders a line knows; the secret stays a placeholder
    unless the caller passes the one just revealed. */
export function fill(text: string, v: { sipUri?: string | null; projectId?: string | null; secret?: string | null; aiExt?: string }): string {
  return text
    .replaceAll("{SIP_URI}", v.sipUri || "sip:PROJECT_ID@sip.voice.clefs.app;transport=tls")
    .replaceAll("{PROJECT_ID}", v.projectId || "PROJECT_ID")
    .replaceAll("{SECRET}", v.secret || "YOUR_LINE_SECRET")
    .replaceAll("{AI_EXT}", v.aiExt || "8000");
}
