/** Channel codes read as names on the weekly pulse (QA OWN-13). Run alone
 *  from this folder: node --test channelLabel.test.mjs */
import { test } from "node:test";
import assert from "node:assert/strict";
import { channelLabel } from "../lib.ts";

test("known codes map to names, unknown ones are title-cased, never raw", () => {
  assert.equal(channelLabel("BOOKING"), "Booking.com");
  assert.equal(channelLabel("DIRECT_WEB"), "Direct web");
  assert.equal(channelLabel("direct_web"), "Direct web");
  assert.equal(channelLabel("TRAVEL_CLUB"), "Travel club");
  assert.equal(channelLabel(null), "Unknown channel");
});
