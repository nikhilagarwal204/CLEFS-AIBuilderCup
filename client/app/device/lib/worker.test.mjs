// The worker screen's pure parts (05 spec, staff app): the photo downsizing
// parameters, the proof / reply / duty request and response shapes, the
// card rules behind URGENT, Emergency and "Done today", the voice-note
// helpers and the staff strings. Node's runner with TS stripping:
//   cd client && node --test app/device/lib/worker.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PROOF_JPEG_QUALITY, PROOF_MAX_BYTES, PROOF_MAX_EDGE, PROOF_MIME, PROOF_RETRY_QUALITY, canSendAsIs, fitWithin, proofEncodePlan,
} from "./proofImage.ts";
import {
  DUTY_PATH, PROOF_FIELD, buildProofForm, countDoneToday, dateKey, deptKeyOf, dutyBody, englishText, escalationRole, firstName,
  guestWords, isEmergency, isMine, isPriorityUrgent, jobText, needsProof, parseDutyResult, parseMe, parseProofResult, parseReplyResult,
  proofInReview, proofOutcome, proofPath, replyBody, replyPath, slaInfo, sortForWorker,
} from "./worker.ts";
import { AUDIO_MIME_PREFERENCE, baseMime, blobToBase64, bytesToBase64, pickAudioMime } from "./voiceNote.ts";
import {
  STAFF_DICTS, STAFF_LANGS, agoText, durationText, languageName, placeholders, resolveStaffLang, stateText, staffDir, staffT, toStaffLang,
} from "./i18n.ts";

const card = (over = {}) => ({
  id: 7, room: "204", type: "Towel", description: "Guest requests a towel urgently in the room.", category: "Housekeeping",
  priority: "medium", timestamp: 0, state: "assigned", version: 1, ...over,
});

// ─── Photo downsizing (decision 9: <= 1280 px, JPEG 0.8, via a canvas) ───

test("proof photos are JPEG at quality 0.8 and at most 1280 px on the long edge", () => {
  assert.equal(PROOF_MAX_EDGE, 1280);
  assert.equal(PROOF_JPEG_QUALITY, 0.8);
  assert.equal(PROOF_MIME, "image/jpeg");
  assert.equal(PROOF_MAX_BYTES, 3 * 1024 * 1024, "the proof route's limit");
  assert.ok(PROOF_RETRY_QUALITY < PROOF_JPEG_QUALITY);
  assert.deepEqual(proofEncodePlan(4032, 3024), { width: 1280, height: 960, scale: 1280 / 4032, mime: "image/jpeg", quality: 0.8 });
});

test("downsizing keeps the aspect ratio for landscape, portrait and square shots", () => {
  assert.deepEqual(fitWithin(4032, 3024), { width: 1280, height: 960, scale: 1280 / 4032 });
  assert.deepEqual(fitWithin(3024, 4032), { width: 960, height: 1280, scale: 1280 / 4032 });
  assert.deepEqual(fitWithin(5000, 5000), { width: 1280, height: 1280, scale: 0.256 });
  const p = fitWithin(4000, 2250); // 16:9
  assert.equal(p.width, 1280);
  assert.equal(p.height, 720);
});

test("small photos are never upscaled, and a sliver keeps at least 1 px", () => {
  assert.deepEqual(fitWithin(800, 600), { width: 800, height: 600, scale: 1 });
  assert.deepEqual(fitWithin(1280, 1), { width: 1280, height: 1, scale: 1 });
  assert.deepEqual(fitWithin(100000, 10), { width: 1280, height: 1, scale: 0.0128 });
  assert.throws(() => fitWithin(0, 100));
  assert.throws(() => fitWithin(NaN, 100));
});

test("only JPEG or PNG under 3 MB may go up unchanged when the phone cannot redraw it", () => {
  assert.equal(canSendAsIs({ type: "image/jpeg", size: 900_000 }), true);
  assert.equal(canSendAsIs({ type: "image/png", size: 2_000_000 }), true);
  assert.equal(canSendAsIs({ type: "image/heic", size: 900_000 }), false);
  assert.equal(canSendAsIs({ type: "image/jpeg", size: PROOF_MAX_BYTES + 1 }), false);
  assert.equal(canSendAsIs({ type: "image/jpeg", size: 0 }), false);
});

// ─── Request shapes (05 spec, REST "Staff app") ───

test("proof and reply paths live under /device/service/requests/{id}", () => {
  assert.equal(proofPath(42), "/device/service/requests/42/proof");
  assert.equal(replyPath(42), "/device/service/requests/42/reply");
  assert.equal(DUTY_PATH, "/device/service/duty");
  assert.throws(() => proofPath(0));
  assert.throws(() => replyPath(1.5));
});

test("the proof upload is multipart with the photo under 'photo'", async () => {
  const jpeg = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" });
  const form = buildProofForm(jpeg, { clientEventId: "8f0e2a52-4a0c-4c1e-9d3b-6c3a2f1b0e77" });
  assert.equal(PROOF_FIELD, "photo");
  const photo = form.get("photo");
  assert.ok(photo instanceof Blob);
  assert.equal(photo.name, "proof.jpg");
  assert.equal(photo.type, "image/jpeg");
  assert.equal(photo.size, 4);
  assert.equal(form.get("client_event_id"), "8f0e2a52-4a0c-4c1e-9d3b-6c3a2f1b0e77");
  assert.equal(buildProofForm(jpeg).get("client_event_id"), null, "no key unless given");
  assert.deepEqual([...form.keys()].sort(), ["client_event_id", "photo"]);
});

test("reply bodies: {text} trimmed, or {audio_b64, mime} with a bare audio mime", () => {
  assert.deepEqual(replyBody({ text: "  On my way with the towels  " }), { text: "On my way with the towels" });
  assert.equal(replyBody({ text: "   " }), null);
  assert.deepEqual(replyBody({ audio_b64: "GkXfow==", mime: "audio/webm;codecs=opus" }), { audio_b64: "GkXfow==", mime: "audio/webm" });
  assert.deepEqual(replyBody({ audio_b64: "AAAA", mime: "Audio/MP4" }), { audio_b64: "AAAA", mime: "audio/mp4" });
  assert.equal(replyBody({ audio_b64: "", mime: "audio/ogg" }), null);
  assert.equal(replyBody({ audio_b64: "AAAA", mime: "video/webm" }), null);
});

test("duty body is {on_duty}", () => {
  assert.deepEqual(dutyBody(false), { on_duty: false });
  assert.deepEqual(dutyBody(true), { on_duty: true });
});

// ─── Response shapes ───

test("a matching photo closes the job", () => {
  const req = card({ state: "completed", proof_status: "verified" });
  const r = parseProofResult({ verified: true, reason: "Two towels on the bed", reason_en: "Two towels on the bed", completed: true,
    proof_status: "verified", replayed: false, request: req });
  assert.deepEqual(r, { verified: true, completed: true, status: "verified", reason: "Two towels on the bed", request: req });
  assert.equal(proofOutcome(r), "verified");
  // A server that leaves out `completed` but returns the closed request still counts.
  assert.equal(parseProofResult({ verified: true, request: req }).completed, true);
});

test("a photo that does not match keeps the job open and says why, in the worker's language", () => {
  const req = card({ state: "acknowledged", proof_status: "rejected", proof_reason: "No towel is visible" });
  const r = parseProofResult({ verified: false, reason: "Walang tuwalyang makikita.", reason_en: "No towel is visible",
    completed: false, proof_status: "rejected", request: req });
  assert.equal(r.verified, false);
  assert.equal(r.completed, false);
  assert.equal(r.reason, "Walang tuwalyang makikita.", "the worker's language, not reason_en");
  assert.equal(proofOutcome(r), "rejected");
  assert.equal(parseProofResult({ verified: false, request: req }).reason, "No towel is visible", "falls back to the card's reason");
  assert.equal(proofOutcome({ verified: false, status: null }), "rejected", "the contract's plain mismatch");
});

test("an undecided check goes to the supervisor, not 'does not match'", () => {
  const r = parseProofResult({ verified: false, completed: false, proof_status: "unverified",
    reason: "The photo could not be checked automatically. Your supervisor has been asked to look at it." });
  assert.equal(r.status, "unverified");
  assert.equal(proofOutcome(r), "review");
  assert.equal(proofInReview(card({ proof_status: "unverified" })), true);
  assert.equal(proofInReview(card({ proof_status: "rejected" })), false);
});

test("odd proof answers parse to a safe 'not verified'", () => {
  assert.deepEqual(parseProofResult(null), { verified: false, completed: false, status: null, reason: null, request: null });
  assert.deepEqual(parseProofResult({ verified: "yes", request: { id: "x" } }),
    { verified: false, completed: false, status: null, reason: null, request: null });
});

test("/me gives the greeting, the reading language, duty and the counters", () => {
  const me = parseMe({
    staff: { id: 11, name: "Maria Santos", first_name: "Maria", role: "Room Attendant", role_key: "attendant",
      department: "Housekeeping", department_key: "housekeeping", languages: ["fil", "en"], reading_language: "fil" },
    on_duty: false, shift: null, counts: { active: 2, done_today: 3 },
  });
  assert.deepEqual(me, {
    staff: { id: 11, name: "Maria Santos", first_name: "Maria", role: "Room Attendant", department: "Housekeeping",
      department_key: "housekeeping", reading_language: "fil" },
    on_duty: false, counts: { active: 2, done_today: 3 },
  });
  assert.equal(parseMe({ staff: { id: 4, name: "Priya", languages: ["hi"] } }).staff.reading_language, "hi", "falls back to languages[0]");
  assert.equal(parseMe({ staff: { id: 4, name: "Priya" } }).on_duty, true, "on duty unless the server says otherwise");
  assert.deepEqual(parseMe({ staff: { id: 4 } }).counts, { active: 0, done_today: 0 });
  assert.equal(parseMe({}), null);
  assert.equal(parseMe(null), null);
});

test("reply answers carry what the guest will read", () => {
  assert.deepEqual(
    parseReplyResult({ text_original: "Dadalhin ko na po", text_for_guest: "سأحضرها الآن", language: "ar" }),
    { text_original: "Dadalhin ko na po", text_for_guest: "سأحضرها الآن", language: "ar" },
  );
  assert.deepEqual(parseReplyResult({ text_original: "Coming now" }), { text_original: "Coming now", text_for_guest: "Coming now", language: null });
  assert.equal(parseDutyResult({ on_duty: false }, true), false);
  assert.equal(parseDutyResult({ ok: true }, false), false, "the asked state when the server does not say");
});

// ─── Card rules ───

test("URGENT is priority high; Emergency is L1, an emergency sensitivity or type", () => {
  assert.equal(isPriorityUrgent(card({ priority: "high" })), true);
  assert.equal(isPriorityUrgent(card({ priority: "HIGH" })), true);
  assert.equal(isPriorityUrgent(card({ priority: "medium", severity: "L1" })), false, "severity is not URGENT");
  assert.equal(isEmergency(card({ severity: "L1" })), true);
  assert.equal(isEmergency(card({ sensitivity: "emergency" })), true);
  assert.equal(isEmergency(card({ request_type_key: "emergency" })), true);
  assert.equal(isEmergency(card({ priority: "high" })), false);
  // The server's own flags (router.card: urgent, emergency) count too.
  assert.equal(isPriorityUrgent(card({ urgent: true })), true);
  assert.equal(isEmergency(card({ emergency: true })), true);
});

test("only needs_proof === true asks for a photo (older servers complete as before)", () => {
  assert.equal(needsProof(card({ needs_proof: true })), true);
  assert.equal(needsProof(card({ needs_proof: false })), false);
  assert.equal(needsProof(card()), false);
});

test("the job reads in the worker's language, with English and the guest's words one tap away", () => {
  const c = card({ display_text: "Humihingi ng tuwalya ang bisita.", original_text: "أحتاج منشفة من فضلك", guest_language: "ar" });
  assert.equal(jobText(c), "Humihingi ng tuwalya ang bisita.");
  assert.equal(englishText(c), "Guest requests a towel urgently in the room.");
  assert.equal(guestWords(c), "أحتاج منشفة من فضلك");
  const plain = card();
  assert.equal(jobText(plain), "Guest requests a towel urgently in the room.", "no translation: the English text");
  assert.equal(englishText(plain), null);
  assert.equal(guestWords(card({ original_text: "Guest requests a towel urgently in the room." })), null, "same words are not repeated");
});

test("mine is the person's own job or the device's unclaimed pool", () => {
  assert.equal(isMine(card({ assigned_staff_id: 3 }), 3, 9), true);
  assert.equal(isMine(card({ assigned_staff_id: 4 }), 3, 9), false);
  assert.equal(isMine(card({ assigned_staff_id: null, assigned_device_id: 9 }), 3, 9), true);
  assert.equal(isMine(card({ assigned_staff_id: 4, assigned_device_id: 9 }), 3, 9), false);
});

test("emergencies, then URGENT, keep the server's order otherwise", () => {
  const list = [
    card({ id: 1 }), card({ id: 2, priority: "high" }), card({ id: 3 }), card({ id: 4, severity: "L1" }), card({ id: 5, priority: "high" }),
  ];
  assert.deepEqual(sortForWorker(list).map(c => c.id), [4, 2, 5, 1, 3]);
});

test("department names fold to the server's keys", () => {
  assert.equal(deptKeyOf("Housekeeping"), "housekeeping");
  assert.equal(deptKeyOf("Food & Beverage"), "ird");
  assert.equal(deptKeyOf("Front Desk"), "front_office");
  assert.equal(deptKeyOf("front_office"), "front_office");
  assert.equal(deptKeyOf("Maintenance"), "engineering");
  assert.equal(deptKeyOf("Spa"), "concierge");
  assert.equal(deptKeyOf("Astronomy"), null);
  assert.equal(deptKeyOf(null), null);
});

test("done today counts this person's completions on the hotel's calendar day", () => {
  const tz = "Asia/Dubai"; // UTC+4
  const now = Date.UTC(2026, 9, 19, 10, 0); // 14:00 in Dubai
  const closed = [
    card({ id: 1, state: "completed", assigned_staff_id: 3, resolved_at: Date.UTC(2026, 9, 19, 6, 0) }), // 10:00 today
    card({ id: 2, state: "completed", assigned_staff_id: 3, resolved_at: Date.UTC(2026, 9, 18, 21, 0) }), // 01:00 today
    card({ id: 3, state: "completed", assigned_staff_id: 3, resolved_at: Date.UTC(2026, 9, 18, 19, 0) }), // 23:00 yesterday
    card({ id: 4, state: "completed", assigned_staff_id: 8, completed_by_staff_id: 3, resolved_at: Date.UTC(2026, 9, 19, 7, 0) }),
    card({ id: 5, state: "completed", assigned_staff_id: 3, completed_by_staff_id: 8, resolved_at: Date.UTC(2026, 9, 19, 7, 0) }),
    card({ id: 6, state: "cancelled", assigned_staff_id: 3, resolved_at: Date.UTC(2026, 9, 19, 7, 0) }),
    card({ id: 7, state: "completed", assigned_staff_id: 3, resolved_at: new Date(Date.UTC(2026, 9, 19, 8, 0)).toISOString() }),
  ];
  assert.equal(countDoneToday(closed, 3, tz, now), 4); // 1, 2, 4 (finished by 3), 7 (ISO time)
  assert.equal(dateKey(Date.UTC(2026, 9, 18, 21, 0), tz), "2026-10-19");
  assert.equal(dateKey(Date.UTC(2026, 9, 18, 21, 0), "Not/AZone"), "2026-10-18", "a bad zone falls back to UTC");
});

test("first names, SLA chips and escalation rungs", () => {
  assert.equal(firstName("Maria Santos"), "Maria");
  assert.equal(firstName("Priya Sharma (DEMO)"), "Priya");
  assert.equal(firstName(""), "");
  assert.deepEqual(slaInfo(card({ state: "assigned", ack_due_at: 100_000 }), 40_000), { kind: "ackIn", ms: 60_000, tone: "neutral" });
  assert.deepEqual(slaInfo(card({ state: "assigned", ack_due_at: 100_000 }), 130_000), { kind: "ackLate", ms: 30_000, tone: "bad" });
  assert.deepEqual(slaInfo(card({ state: "acknowledged", resolve_due_at: 1_000_000 }), 900_000), { kind: "dueIn", ms: 100_000, tone: "warn" });
  assert.equal(slaInfo(card({ state: "completed", resolve_due_at: 1 }), 2), null);
  assert.equal(escalationRole(1), "supervisor");
  assert.equal(escalationRole("duty_manager"), "duty_manager");
  assert.equal(escalationRole(3), "gm");
  assert.equal(escalationRole(0), null);
});

// ─── Voice notes ───

test("the recorder prefers WebM/Opus, then Ogg, then MP4 (Safari)", () => {
  assert.equal(AUDIO_MIME_PREFERENCE[0], "audio/webm;codecs=opus");
  const chrome = new Set(["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]);
  const firefox = new Set(["audio/ogg;codecs=opus", "audio/ogg", "audio/webm"]);
  const safari = new Set(["audio/mp4", "audio/aac"]);
  assert.equal(pickAudioMime(m => chrome.has(m)), "audio/webm;codecs=opus");
  assert.equal(pickAudioMime(m => firefox.has(m)), "audio/webm");
  assert.equal(pickAudioMime(m => safari.has(m)), "audio/mp4");
  assert.equal(pickAudioMime(() => false), null);
  assert.equal(pickAudioMime(null), null);
  assert.equal(pickAudioMime(() => { throw new Error("probe"); }), null, "a throwing probe means no");
  assert.equal(baseMime("audio/webm;codecs=opus"), "audio/webm");
  assert.equal(baseMime("audio/mp4; codecs=\"mp4a.40.2\""), "audio/mp4");
  assert.equal(baseMime(undefined), "");
});

test("voice notes are standard base64, even past one chunk", async () => {
  const small = new Uint8Array([0, 1, 2, 250, 255]);
  assert.equal(bytesToBase64(small), Buffer.from(small).toString("base64"));
  const big = new Uint8Array(100_003).map((_, i) => (i * 31) % 256);
  assert.equal(bytesToBase64(big), Buffer.from(big).toString("base64"));
  assert.equal(await blobToBase64(new Blob([big], { type: "audio/webm" })), Buffer.from(big).toString("base64"));
});

// ─── Staff strings ───

test("every staff language has every string, with the same placeholders", () => {
  const en = STAFF_DICTS.en;
  for (const lang of STAFF_LANGS) {
    const dict = STAFF_DICTS[lang];
    assert.deepEqual(Object.keys(dict).sort(), Object.keys(en).sort(), `${lang} keys`);
    for (const [k, v] of Object.entries(en)) {
      assert.ok(typeof dict[k] === "string" && dict[k].trim(), `${lang}.${k} is empty`);
      assert.deepEqual(placeholders(dict[k]), placeholders(v), `${lang}.${k} placeholders`);
    }
  }
});

test("no staff string names an AI vendor other than Gemini", () => {
  // Built from pieces so this file never spells them either (05 spec, decision 18).
  const banned = [["open", "ai"].join(""), ["chat", "g", "pt"].join(""), ["g", "pt"].join("")];
  for (const lang of STAFF_LANGS) {
    for (const [k, v] of Object.entries(STAFF_DICTS[lang])) {
      for (const word of banned) assert.ok(!v.toLowerCase().includes(word), `${lang}.${k}`);
    }
  }
});

test("roster language codes map to the staff dictionaries", () => {
  assert.equal(toStaffLang("fil"), "fil");
  assert.equal(toStaffLang("tl"), "fil");
  assert.equal(toStaffLang("fil-PH"), "fil");
  assert.equal(toStaffLang("hi-IN"), "hi");
  assert.equal(toStaffLang("AR"), "ar");
  assert.equal(toStaffLang("ml"), null);
  assert.equal(toStaffLang(""), null);
});

test("an explicit pick beats the roster language, which beats the browser", () => {
  assert.equal(resolveStaffLang({ stored: "hi", server: "fil", browser: ["ar"] }), "hi");
  assert.equal(resolveStaffLang({ stored: null, server: "tl", browser: ["ar"] }), "fil");
  assert.equal(resolveStaffLang({ server: "ml", browser: ["xx", "ur-PK"] }), "ur");
  assert.equal(resolveStaffLang({}), "en");
});

test("lookups interpolate, fall back to English, and Arabic and Urdu read right to left", () => {
  const fil = staffT("fil");
  assert.equal(fil("hi", { name: "Maria" }), "Kumusta, Maria");
  assert.equal(fil("job.room", { room: "204" }), "Kuwarto 204");
  assert.equal(staffT("ml")("action.markDone"), "Mark as Done", "no dictionary: English");
  assert.equal(staffT("en")("proof.rejected", { reason: "No towel is visible" }), "Photo doesn't match: No towel is visible");
  assert.equal(staffDir("ar"), "rtl");
  assert.equal(staffDir("ur-PK"), "rtl");
  assert.equal(staffDir("hi"), "ltr");
  assert.equal(stateText("acknowledged", staffT("en")), "Accepted");
  assert.equal(stateText("on_hold", staffT("en")), "on hold");
});

test("times and language names come out in the worker's language", () => {
  const t = staffT("en");
  assert.equal(agoText(1_000, 30_000, "en", t), "Just now");
  assert.equal(agoText(0, 3 * 60_000, "en", t), "3m ago");
  assert.equal(agoText(0, 2 * 3_600_000, "hi", staffT("hi")).includes("2"), true);
  assert.equal(durationText(5 * 60_000, "en"), "5m");
  assert.equal(durationText(65 * 60_000, "en"), "1h 5m");
  assert.equal(durationText(20_000, "en"), "1m", "a countdown never reads 0m");
  assert.equal(durationText(4 * 60_000 + 1_000, "en"), "5m", "rounded up");
  assert.equal(languageName("ar", "en"), "Arabic");
  assert.equal(languageName("ar", "hi"), "अरबी");
  assert.equal(languageName(null, "en"), null);
});
