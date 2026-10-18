/**
 * Inquiry widget embedding check (04 spec 4.3 web form). The widget hides
 * itself on a site the hotel did not list, and must never hide on the
 * hotel's own site because a browser gave less information than expected.
 * `npm test` picks this up; alone: node --test embed.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { embedderOrigin, normalizeOrigin, originAllowed } from "../embed.ts";

test("embedderOrigin prefers ancestorOrigins, then the referrer", () => {
  assert.equal(embedderOrigin(false, ["https://hotel.ae"], "https://x.com/"), null, "not framed");
  assert.equal(embedderOrigin(true, ["https://Hotel.ae"], "https://x.com/"), "https://hotel.ae");
  assert.equal(embedderOrigin(true, [], "https://hotel.ae/rooms?utm_source=g"), "https://hotel.ae");
  assert.equal(embedderOrigin(true, null, ""), null);
});

test("normalizeOrigin keeps scheme, host and port only", () => {
  assert.equal(normalizeOrigin("https://www.hotel.ae:8443/a/b"), "https://www.hotel.ae:8443");
  assert.equal(normalizeOrigin("javascript:alert(1)"), null);
  assert.equal(normalizeOrigin("not a url"), null);
});

test("originAllowed", () => {
  assert.equal(originAllowed("https://evil.example", []), true, "no list = anywhere");
  assert.equal(originAllowed(null, ["https://hotel.ae"]), true, "unknown host is not refused");
  assert.equal(originAllowed("https://hotel.ae", ["https://hotel.ae/"]), true);
  assert.equal(originAllowed("https://www.hotel.ae", ["https://hotel.ae"]), false, "exact origin, no wildcard");
  assert.equal(originAllowed("https://evil.example", ["https://hotel.ae"]), false);
});
