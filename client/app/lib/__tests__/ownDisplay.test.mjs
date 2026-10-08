// Owner-facing display fixes (QA OWN-13): no raw minor units, ids or
// trailing-zero Decimals. Run: cd client && node --test app/lib/__tests__/ownDisplay.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { commitSummary } from "../../ingest/summary.ts";

const money = (minor, cur) => `${cur} ${(minor / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

test("an import's commit result drops ids and formats minor units as money", () => {
  const out = commitSummary({ statement_id: 1, doc_no: "S-1", lines: 245, commission_minor: 4801391, currency: "AED",
    matches: 3, dry_run: false }, "USD", money);
  assert.equal(out, "245 lines, 3 matches, commission AED 48,013.91");
  assert.ok(!/statement id|minor/.test(out));
  assert.equal(commitSummary(null, "AED", money), "");
});

test("finding calc inputs drop trailing Decimal zeros and name a bare pct", async () => {
  const { calcInputs, tidyNumber } = await import("../../app/[hotelSlug]/(console)/profit/lib.ts");
  assert.equal(tidyNumber("18.00000000"), "18");
  assert.equal(tidyNumber("0.150"), "0.15");
  assert.equal(tidyNumber("100"), "100");
  assert.equal(tidyNumber("BOOKING"), "BOOKING");
  const [row] = calcInputs({ inputs: { pct: "18.00000000" } });
  assert.deepEqual([row.label, row.text], ["Percent", "18"]);
});
