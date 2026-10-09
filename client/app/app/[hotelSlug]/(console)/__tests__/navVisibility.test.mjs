/**
 * Console focus (05 spec 2.16, 2.17): a module switched off with
 * CLEFS_DISABLED_MODULES (gone from the context's modules_enabled) or for
 * the hotel never shows a tab; owners and GMs land on the service desk.
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test navVisibility.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { landingTarget, visibleNav } from "../navVisibility.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NAV_SRC = fs.readFileSync(path.resolve(HERE, "..", "nav.ts"), "utf8");
const SHELL = fs.readFileSync(path.resolve(HERE, "..", "ConsoleShell.tsx"), "utf8");
const LANDING = fs.readFileSync(path.resolve(HERE, "..", "page.tsx"), "utf8");

const ITEMS = [
  { key: "brief", perm: "brief.view", module: "brief" },
  { key: "service", perm: "service.view", module: "service" },
  { key: "concierge", perm: "concierge.view", module: "concierge" },
  { key: "profit", perm: "profit.view", module: "profit_guard" },
  { key: "trust", perm: "trust.view", module: "trust" },
  { key: "settings", perm: "core.settings.view", module: "core" },
];

/** hotel.moduleEnabled as lib/hotel.tsx computes it from modules_enabled. */
const moduleEnabled = enabled => m => ["core", "settings"].includes(m) || enabled.length === 0 || enabled.includes(m);
const admin = { can: () => true, legacyMode: false, contextReady: true };

test("a module the server did not load has no tab, even for an admin who holds every permission", () => {
  // CLEFS_DISABLED_MODULES=brief,profit_guard: the context lists only what loaded.
  const v = visibleNav(ITEMS, { ...admin, moduleEnabled: moduleEnabled(["service", "concierge", "trust"]) });
  assert.deepEqual(v.map(n => n.key), ["service", "concierge", "trust", "settings"]);
});

test("a permission the server never registered hides the tab too", () => {
  const held = new Set(["service.view", "concierge.view", "core.settings.view"]);
  const v = visibleNav(ITEMS, { can: p => held.has(p), moduleEnabled: () => true, legacyMode: false, contextReady: true });
  assert.deepEqual(v.map(n => n.key), ["service", "concierge", "settings"]);
});

test("no tabs until the hotel context has answered (moduleEnabled says yes to everything before it)", () => {
  assert.deepEqual(visibleNav(ITEMS, { ...admin, contextReady: false, moduleEnabled: moduleEnabled([]) }), []);
});

test("anonymous legacy access still reaches the service desk", () => {
  const v = visibleNav(ITEMS, { can: () => false, moduleEnabled: () => true, legacyMode: true, contextReady: true });
  assert.deepEqual(v.map(n => n.key), ["service"]);
});

test("the landing falls back to the service desk when the role's own module is off", () => {
  const visible = ITEMS.filter(n => ["service", "trust", "settings"].includes(n.key));
  assert.equal(landingTarget(visible, "profit").key, "service");
  assert.equal(landingTarget(visible, "trust").key, "trust");
  assert.equal(landingTarget([ITEMS[4]], "profit").key, "trust");
  assert.equal(landingTarget([], "service"), undefined);
});

test("owners, GMs and admins land on the service desk", () => {
  const body = NAV_SRC.slice(NAV_SRC.indexOf("export function landingKey"));
  assert.match(body, /if \(opts\.legacyAnonymous \|\| opts\.isAdmin\) return "service";/);
  assert.doesNotMatch(body, /return "brief"/);
  assert.doesNotMatch(body, /case "owner"|case "gm"/); // they take the default: "service"
  assert.match(body, /default:\s*return "service";/);
});

test("the shell and the landing page both filter through visibleNav", () => {
  assert.match(SHELL, /visibleNav\(NAV, \{/);
  assert.match(SHELL, /contextReady: Boolean\(hotel\.context\) \|\| Boolean\(hotel\.error\)/);
  assert.match(LANDING, /visibleNav\(NAV, \{/);
  assert.match(LANDING, /landingTarget\(visible, want\)/);
});

test("nav.ts still declares all 15 modules (server contract tests read it)", () => {
  const entries = NAV_SRC.match(/\{ key: "[a-z]+",[^{}]*href: "[a-z-]+", perm: "[a-z_.]+", module: "[a-z_]+"/g) ?? [];
  assert.equal(entries.length, 15);
});
