/**
 * Console new-request shortcut (03 spec 1.8) against WCAG 2.1.4: a bare
 * letter anywhere in the console used to navigate to /service/new, so a
 * stray "n" from dictation or a key pressed on a focused row or tab carried
 * the user off their page. The shortcut is now Alt+N (Option+N on a Mac),
 * stands aside inside composite widgets, and is named in the user menu.
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test newRequestShortcut.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  NEW_REQUEST_SHORTCUT, isNewRequestChord, isShortcutExcludedTarget,
  newRequestShortcutLabel, shouldOpenNewRequest,
} from "../newRequestShortcut.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHELL = fs.readFileSync(path.resolve(HERE, "..", "ConsoleShell.tsx"), "utf8");

// ─── Fakes ───

const key = (k, mods = {}) => ({
  key: k, code: mods.code ?? (/^[a-z]$/i.test(k) ? `Key${k.toUpperCase()}` : ""),
  altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, ...mods,
});

/** A focused element whose ancestors match the given selectors. */
const el = (tagName, within = []) => ({
  tagName, isContentEditable: false,
  closest: sel => (sel.split(",").map(s => s.trim()).some(s => within.includes(s) || s === tagName.toLowerCase()) ? {} : null),
});

const BODY = el("BODY");
const FREE = { dialogOpen: false, onNewRequestPage: false };

// ─── The regression: a bare letter never navigates ───

test("a bare n or N is not the shortcut", () => {
  assert.equal(shouldOpenNewRequest(key("n"), BODY, FREE), false);
  assert.equal(shouldOpenNewRequest(key("N", { shiftKey: true }), BODY, FREE), false);
});

test("a bare n on a focused table row, tab or button stays put", () => {
  for (const t of [el("TR"), el("BUTTON", ["[role='tablist']"]), el("BUTTON")]) {
    assert.equal(shouldOpenNewRequest(key("n"), t, FREE), false);
  }
});

// ─── The chord ───

test("Alt+N opens the form from the page body", () => {
  assert.equal(shouldOpenNewRequest(key("n", { altKey: true }), BODY, FREE), true);
  assert.equal(shouldOpenNewRequest(key("N", { altKey: true }), BODY, FREE), true);
});

test("Option+N on a Mac (tilde dead key) still matches by physical key", () => {
  assert.equal(isNewRequestChord(key("Dead", { altKey: true, code: "KeyN" })), true);
  assert.equal(isNewRequestChord(key("˜", { altKey: true, code: "KeyN" })), true);
  assert.equal(isNewRequestChord(key("Dead", { altKey: true, code: "KeyE" })), false);
});

test("extra modifiers belong to the browser or OS", () => {
  for (const mods of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }]) {
    assert.equal(isNewRequestChord(key("n", { altKey: true, ...mods })), false);
  }
  assert.equal(isNewRequestChord(key("n", { ctrlKey: true })), false);
});

test("repeats, IME composition and handled events are ignored", () => {
  for (const extra of [{ repeat: true }, { isComposing: true }, { defaultPrevented: true }]) {
    assert.equal(shouldOpenNewRequest(key("n", { altKey: true, ...extra }), BODY, FREE), false);
  }
});

// ─── Scoping ───

test("typing targets and composite widgets keep the chord", () => {
  const typing = [el("INPUT"), el("TEXTAREA"), el("SELECT"), { ...el("DIV"), isContentEditable: true }];
  const widgets = ["[role='grid']", "[role='listbox']", "[role='tablist']", "[role='menu']", "[role='tree']", "tr"]
    .map(w => el("DIV", [w]));
  for (const t of [...typing, ...widgets]) {
    assert.equal(isShortcutExcludedTarget(t), true, JSON.stringify(t.tagName));
    assert.equal(shouldOpenNewRequest(key("n", { altKey: true }), t, FREE), false);
  }
  assert.equal(isShortcutExcludedTarget(null), false);
  assert.equal(isShortcutExcludedTarget(el("BUTTON")), false);
});

test("an open dialog or the form itself suppresses it", () => {
  const alt = key("n", { altKey: true });
  assert.equal(shouldOpenNewRequest(alt, BODY, { dialogOpen: true, onNewRequestPage: false }), false);
  assert.equal(shouldOpenNewRequest(alt, BODY, { dialogOpen: false, onNewRequestPage: true }), false);
});

// ─── Discoverability and wiring ───

test("the hint names the key the way each platform labels it", () => {
  assert.equal(NEW_REQUEST_SHORTCUT, "Alt+N");
  assert.equal(newRequestShortcutLabel("MacIntel"), "Option+N");
  assert.equal(newRequestShortcutLabel("iPad"), "Option+N");
  assert.equal(newRequestShortcutLabel("Win32"), "Alt+N");
  assert.equal(newRequestShortcutLabel(undefined), "Alt+N");
});

test("the shell routes keydown through the tested rule, not a bare-letter check", () => {
  assert.match(SHELL, /shouldOpenNewRequest\(e,/);
  assert.doesNotMatch(SHELL, /e\.key !== "n" && e\.key !== "N"/);
});

test("the user menu documents the shortcut", () => {
  assert.match(SHELL, /aria-keyshortcuts=\{NEW_REQUEST_SHORTCUT\}/);
  assert.match(SHELL, /<kbd className="cx-usermenu__kbd">\{newRequestShortcutLabel\(/);
});
