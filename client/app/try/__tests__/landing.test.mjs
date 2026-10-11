/**
 * The site root, as AI Builder Cup judges see it (they only ever open the
 * bare URL): the avatar hero first, then "Try it yourself", then the story;
 * plain words with no system terms; and nothing from the old demo hotel.
 * Reads the sources, so it runs without a build. `npm test` picks it up;
 * alone: node --test app/try/__tests__/landing.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const CLIENT = path.resolve(APP, "..");
const read = (rel) => fs.readFileSync(path.join(APP, rel), "utf8");

// Copy only: comments may explain history, the page may not show it.
const stripComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const LANDING = ["page.tsx", "MarketingHomepage.tsx", "try/TryDemo.tsx", "try/demo.ts", "lib/faq.ts"];

test("the hero comes first, then Try it yourself, then the story", () => {
  const home = read("MarketingHomepage.tsx");
  const order = [
    'className="cb-nav', 'id="top"', "<TryDemo />", 'id="how"', 'id="wrong"', 'id="problem"', 'id="why"',
    'id="features"', 'id="pricing"', 'id="trust"', 'id="questions"', "<footer",
  ];
  const at = order.map((m) => home.indexOf(m));
  at.forEach((i, k) => assert.ok(i >= 0, `missing ${order[k]}`));
  for (let k = 1; k < at.length; k++) assert.ok(at[k - 1] < at[k], `${order[k - 1]} must come before ${order[k]}`);
  // The hero's main button and the nav's button go to the doors.
  assert.match(home, /<a href="#try" className="cb-btn cb-btn--gold">[\s\S]{0,80}Try it yourself/);
  assert.match(read("try/TryDemo.tsx"), /<section id="try"/);
  // The root renders the homepage and its JSON-LD.
  const page = read("page.tsx");
  assert.match(page, /<MarketingHomepage today=\{today\} \/>/);
  assert.match(page, /application\/ld\+json/);
});

test("/story and /try go to the root, and their old pages are gone", () => {
  const config = fs.readFileSync(path.join(CLIENT, "next.config.mjs"), "utf8");
  for (const p of ["/story", "/try"]) {
    assert.match(config, new RegExp(`\\{ source: "${p}", destination: "/", permanent: false \\}`));
  }
  assert.equal(fs.existsSync(path.join(APP, "story/page.tsx")), false);
  assert.equal(fs.existsSync(path.join(APP, "try/page.tsx")), false);
});

test("the three doors use short, friendly labels", () => {
  const doors = read("try/TryDemo.tsx");
  for (const label of ["Be the guest in Room", "the housekeeper", "Be the manager", "Open guest page",
                       "'s phone", "Open manager dashboard", "If asked, tap", "Can I get two extra towels?"]) {
    assert.ok(doors.includes(label), `missing "${label}"`);
  }
});

test("no system terms and nothing from the old demo hotel", () => {
  const banned = [
    /shadow mode/i, /pairs? this browser/i, /keyed link/i, /dept_head/, /\bSLAs?\b/, /escalation ladder/i,
    /not live until/i, /tests passed/i, /\bDubai\b/i, /\bDeira\b/i, /Omar Khalid/, /demo-deira-crest/,
    /any language/i,
  ];
  for (const rel of LANDING) {
    const copy = stripComments(read(rel));
    for (const rx of banned) assert.doesNotMatch(copy, rx, `${rel} matches ${rx}`);
  }
});
